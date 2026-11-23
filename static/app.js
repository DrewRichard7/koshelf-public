const $ = id => document.getElementById(id);
const sessionKey = (() => {
  const key = /^#session=([0-9a-f]{64})$/.exec(location.hash)?.[1];
  if (key) history.replaceState(null, '', location.pathname);
  try {
    if (key) sessionStorage.setItem('koshelf-session', key);
    return key || sessionStorage.getItem('koshelf-session');
  } catch {
    return key;
  }
})();
let sessionExpired = !sessionKey;
// Privacy choices from the service; these defaults match it until they load.
let appSettings = {kindleDetection: true, kindleAutoCompare: true, appleBooksProgress: false};

function showSessionPrompt() {
  document.body.classList.add('waiting');
  $('waiting-title').textContent = 'Open KoShelf';
  $('waiting-message').textContent = 'Open the desktop app or run koshelf --browser to start a new local session.';
  $('waiting-status').textContent = 'Local session required';
  $('waiting-choose').hidden = true;
}

async function shelfRequest(path, options = {}) {
  if (!path.startsWith('/api/')) throw Error('Invalid KoShelf request.');
  if (sessionExpired) throw Error('Open KoShelf to start a local session.');
  const headers = new Headers(options.headers);
  headers.set('Authorization', 'Bearer ' + sessionKey);
  const response = await fetch(path, {...options, headers, mode: 'same-origin', redirect: 'error'});
  if (response.status === 403) {
    sessionExpired = true;
    showSessionPrompt();
  }
  return response;
}

let library = {books: [], folders: [], connected: false, candidates: []};
let folder = null;
let ejected = false;
let device = {};
let deviceRequest = 0;
let switchingTo = null;
let comparison = null;
let kindleWarmup = null;
let comparing = false;
let comparedAt = 0;
let polling = false;
let loading = false;
let busy = false;
let dragged = null;
let lastMove = null;
let moveSelection = null;
let metadataSelection = null;
let pendingCover = null;
let coverSelection = 0;
let selectionMode = false;
let selectionRoot = null;
const selectedBooks = new Set();
let visibleBooks = [];
let bulkSaving = false;
let overviewCollapsed = localStorage.getItem('koshelf-storage-collapsed') === 'true';
let storageAnimations = [];
// Generated covers and charts use theme colors from style.css, so they follow the appearance.
const palettes = [1, 2, 3, 4, 5].map(n => [`var(--cover-${n}-bg)`, `var(--cover-${n}-ink)`]);
function el(tag, text, cls) { const n = document.createElement(tag); if(text != null) n.textContent = text; if(cls) n.className=cls; return n; }
const coverRequests = new WeakMap();

function loadCover(image) {
  const request = coverRequests.get(image);
  if (!request) return;
  if (!request.loading) {
    request.loading = (async () => {
      const response = await shelfRequest(request.path);
      if (!response.ok) throw Error('Cover unavailable.');
      const data = await response.blob();
      if (!image.isConnected) return;
      const url = URL.createObjectURL(await shrinkCover(data));
      image.src = url;
      try {
        await image.decode();
        image.style.visibility = '';
      } finally {
        // Shelf cards are reused, so their covers stay in memory until the card is released.
        if (image.closest('#grid')) image.dataset.objectUrl = url;
        else URL.revokeObjectURL(url);
      }
    })().catch(() => image.remove());
  }
  return request.loading;
}

// Shelf tiles are about 165px wide; keeping full-size scans decoded wastes memory.
async function shrinkCover(blob) {
  const width = 480;
  let bitmap;
  try { bitmap = await createImageBitmap(blob); } catch { return blob; }
  try {
    if (bitmap.width <= width * 1.25) return blob;
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = Math.max(1, Math.round(bitmap.height * width / bitmap.width));
    const context = canvas.getContext('2d');
    context.fillStyle = '#e5e4da';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise(resolve => canvas.toBlob(result => resolve(result || blob), 'image/jpeg', 0.88));
  } finally {
    bitmap.close();
  }
}

const coverObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    coverObserver.unobserve(entry.target);
    void loadCover(entry.target.querySelector('img'));
  }
}, {rootMargin: '200px'});

function cover(book, deferred = false) {
  const c = el('div', null, 'cover');
  let hash=0; for(const ch of book.title) hash=(hash*31+ch.charCodeAt(0))|0;
  const colors=palettes[Math.abs(hash)%palettes.length]; c.style.backgroundColor=colors[0];c.style.color=colors[1];
  c.append(el('strong',book.title),el('div',null,'rule'),el('small',book.author));
  if (book.hasCover) {
    const image = el('img');
    image.alt = 'Cover of ' + book.title;
    image.style.visibility = 'hidden';
    const query = new URLSearchParams({path: book.path, root: library.root, v: book.modified, s: book.size});
    coverRequests.set(image, {path: '/api/cover?' + query});
    c.append(image);
    if (!deferred) void loadCover(image);
  }
  return c;
}
function usbWorkflow() {
  if (!library.localFirst || !device.usbKindle || device.kindle) return null;
  return device.usbKindle.mode === 'koreader' ? 'setup' : 'send';
}

function renderConnection() {
  const local=library.localFirst;
  const workflow=usbWorkflow();
  const onMac=library.root===library.macRoot;
  $('status').hidden=local&&(onMac||library.root===device.kindle);
  const onKindle = !!device.kindle && library.root === device.kindle;
  const switcher = $('library-switcher');
  switcher.hidden = !local || !!workflow;
  switcher.dataset.selected = switchingTo === 'Kindle library' ? 'kindle'
    : switchingTo === 'Mac library' ? 'mac'
    : onMac ? 'mac' : onKindle ? 'kindle' : '';
  switcher.setAttribute('aria-busy', String(!!switchingTo));
  for (const [id, current, available] of [
    ['switch-mac', onMac, true],
    ['switch-kindle', onKindle, !!device.kindle]
  ]) {
    const button = $(id);
    button.disabled = !available || busy || loading;
    button.setAttribute('aria-pressed', String(current));
  }
  $('eject').hidden=local?(!device.kindle&&!device.usbKindle&&!library.canEject):!library.canEject;
  $('eject').disabled=busy||loading;
  $('import-books').disabled=busy||loading||!library.connected;
  $('open-sync-history').disabled = busy || loading;
  $('open-icloud-sync').disabled = busy || loading || !library.connected || !onMac;
  $('open-library-health').disabled = busy || loading || !library.connected;
  $('open-genre-cleanup').disabled = busy || loading || !library.connected || !onMac;
  $('open-apple-books').disabled = busy || loading || !library.connected || !onMac;
  $('open-sync-leftovers').disabled = busy || loading || !library.connected || !onMac;
  $('find-duplicates').disabled=busy||loading||!library.connected||!library.books.length;
  renderSelection();
  $('backup').hidden=!workflow&&local&&!device.kindle&&(onMac||!library.canEject);
  $('backup').textContent=workflow==='send'?'Send to Kindle…':workflow==='setup'?'Set up KOReader shelf':
    local&&device.kindle?'Sync libraries':'Back up library';
  $('backup').title=workflow==='send'?'Select EPUBs to convert and send over USB':
    workflow==='setup'?'Create the books folder for KOReader':
    local&&device.kindle?'Review changes on your Mac and Kindle':'Copy the library to ~/KoShelf Library';
  $('backup').disabled=busy||loading||!library.connected||(workflow==='send'&&!library.books.length);
  $('kindle-status').hidden=!local;
  $('kindle-status').textContent=device.syncError?'Could not compare libraries. '+device.syncError:
    device.kindle?(!appSettings.kindleAutoCompare&&!comparison?'KOReader connected':device.syncNeeded?'KOReader connected · sync available':!comparison?'KOReader connected · checking for changes…':'KOReader connected · Libraries are up to date'):
    workflow==='send'?'Kindle reader connected · USB delivery':
    workflow==='setup'?'KOReader detected · shelf setup available':
    ejected?'Kindle safely ejected':'Connect your Kindle over USB';
}
async function checkConnection() {
  const request=++deviceRequest;
  const response=await shelfRequest('/api/status');
  if(!response.ok)throw Error('Could not read connection status');
  const state=await response.json();
  if(request!==deviceRequest||state.busy)return null;
  if (state.usbKindle?.device !== device.usbKindle?.device) {
    if ($('font-dialog').open && !fontWorking) void refreshFonts();
    if ($('kindle-delivery-dialog').open && !busy) void checkKindleDelivery();
  }
  if(state.kindle!==device.kindle){
    comparison=null;comparedAt=0;
    if(state.kindle&&appSettings.kindleAutoCompare){
      const warmup=shelfRequest('/api/warm-kindle').catch(()=>{});
      kindleWarmup=warmup;
      void warmup.finally(()=>{if(kindleWarmup===warmup)kindleWarmup=null;});
    }else kindleWarmup=null;
  }
  device={...state,...comparison};
  if(device.kindle||device.usbKindle)ejected=false;
  renderConnection();
  if(state.kindle&&!kindleWarmup&&appSettings.kindleAutoCompare)void compareLibraries(state.kindle);
  return state;
}
async function compareLibraries(kindle, force=false) {
  // Checks are stat-only for unchanged files; repeat after a minute or when something changes.
  if(comparing||(!force&&Date.now()-comparedAt<60000))return;
  comparing=true;
  let finished=true;
  try{
    const response=await shelfRequest('/api/sync-status');
    if(!response.ok)throw Error('Could not compare libraries.');
    const result=await response.json();
    if(result.cancelled){finished=false;return;}
    if(device.kindle===kindle&&result.kindle===kindle){
      comparison=result;Object.assign(device,result);renderConnection();
    }
  }catch(err){
    if(device.kindle===kindle){comparison={syncError:err.message};Object.assign(device,comparison);renderConnection();}
  }finally{comparing=false;if(device.kindle===kindle&&finished)comparedAt=Date.now();}
}
async function openLibrary(path, present=update=>update()) {
  const response=await shelfRequest('/api/library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'Could not open folder');
  await present(()=>{
    library=data;folder=null;lastMove=null;$('undo').hidden=true;$('search').value='';$('genre-filter').value='';$('series-filter').value='';$('reading-filter').value='';render();
  });
}
async function switchLibrary(event){
  if(busy||loading)return;
  const toKindle=event.currentTarget.id==='switch-kindle';
  const path=toKindle?device.kindle:library.macRoot;
  if(!path || path===library.root)return;
  switchingTo=toKindle?'Kindle library':'Mac library';
  setBusy(true);
  const present=async update=>{
    if(matchMedia('(prefers-reduced-motion: reduce)').matches){update();return;}
    const grid=$('grid');
    const shelf = grid.closest('main').getBoundingClientRect();
    const visibleBooks=()=>[...grid.children].filter(book=>{
      const bounds=book.getBoundingClientRect();
      return bounds.bottom > Math.max(0, shelf.top) && bounds.top < Math.min(innerHeight, shelf.bottom);
    });
    const direction=toKindle?1:-1;
    const animations=[];
    grid.classList.add('shelf-switching');
    try{
      const departing=visibleBooks();
      if(toKindle)departing.reverse();
      animations.push(...departing.map((book,index)=>book.animate([
        {transform:'translateX(0)',opacity:1},
        {transform:`translateX(${-direction*72}px)`,opacity:0}
      ],{duration:390,delay:Math.min(index*32,190),easing:'cubic-bezier(.45,0,.62,1)',fill:'both'})));
      await Promise.all(animations.map(animation=>animation.finished));

      update();
      const arriving=visibleBooks();
      if(!toKindle)arriving.reverse();
      const arrivals=arriving.map((book,index)=>{
        const animation=book.animate([
          {transform:`translateX(${direction*80}px)`,opacity:0},
          {transform:'translateX(0)',opacity:1}
        ],{duration:480,delay:Math.min(index*34,205),easing:'cubic-bezier(.2,.65,.25,1)',fill:'both'});
        animation.pause();
        animation.currentTime=0;
        return animation;
      });
      animations.push(...arrivals);

      const covers=arriving.flatMap(book=>[...book.querySelectorAll('img')]);
      if(covers.length){
        let deadline;
        try{
          await Promise.race([
            Promise.allSettled(covers.map(loadCover)),
            new Promise(resolve=>{deadline=setTimeout(resolve,120);})
          ]);
        }finally{clearTimeout(deadline);}
      }
      for(const animation of arrivals)animation.play();
      await Promise.all(arrivals.map(animation=>animation.finished));
    }finally{
      for(const animation of animations)animation.cancel();
      grid.classList.remove('shelf-switching');
    }
  };
  try{
    if(toKindle&&kindleWarmup)await kindleWarmup;
    await openLibrary(path,present);
    $('notice').textContent='';
  }
  catch(err){$('notice').textContent=err.message;}
  finally{switchingTo=null;setBusy(false);for(const book of $('grid').children)book.draggable=library.connected&&!selectionMode;}
}
$('switch-mac').onclick=switchLibrary;
$('switch-kindle').onclick=switchLibrary;
const untaggedGenre=Symbol('untagged');
const genreCollator=new Intl.Collator(undefined,{sensitivity:'base',numeric:true});
const shelfColors=[1,2,3,4,5,6,7,8].map(n=>`var(--chart-${n})`);
const overviewStrip=$('overview-strip');
for(const [control,card] of [['show-storage','storage'],['show-genres','genre-overview']]){
  $(control).onclick=()=>{
    overviewStrip.scrollTo({
      left:$(card).offsetLeft,
      behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'
    });
  };
}

let finishGenreTransition;

function applyGenreLayout() {
  $('genre-overview').classList.toggle('compact', overviewCollapsed);
  $('genre-details').hidden = overviewCollapsed;
  $('genre-bar').hidden = !overviewCollapsed;
  const toggle = $('genre-toggle');
  const label = overviewCollapsed ? 'Expand genre breakdown' : 'Collapse genre breakdown';
  toggle.setAttribute('aria-expanded', String(!overviewCollapsed));
  toggle.setAttribute('aria-label', label);
  toggle.title = label;
}

function animateGenreLayout() {
  finishGenreTransition?.();
  const panel = $('genre-overview');
  const expanding = !overviewCollapsed;
  const start = panel.getBoundingClientRect();
  const oldPie = $('genre-pie').getBoundingClientRect();
  const oldSegments = [...$('genre-bar').children].map(segment => segment.getBoundingClientRect());
  applyGenreLayout();
  if (panel.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const end = panel.getBoundingClientRect();
  const pie = expanding ? $('genre-pie').getBoundingClientRect() : oldPie;
  const segments = expanding ? oldSegments : [...$('genre-bar').children].map(segment => segment.getBoundingClientRect());
  const overlay = el('div', null, 'genre-morph');
  overlay.setAttribute('aria-hidden', 'true');
  panel.append(overlay);
  const target = $(expanding ? 'genre-pie' : 'genre-bar');
  target.classList.add('genre-morph-target');
  const timing = {duration: 620, easing: 'cubic-bezier(.45,0,.2,1)', fill: 'both'};
  const animations = [panel.animate([
    {height: `${start.height}px`}, {height: `${end.height}px`}
  ], timing)];
  const polygon = points => 'polygon(' + points.map(([x, y]) =>
    `${x - end.left - panel.clientLeft}px ${y - end.top - panel.clientTop}px`).join(',') + ')';

  [...$('genre-pie').children].forEach((slice, index) => {
    const segment = segments[index];
    const angle = Number(slice.dataset.angle);
    const arc = Number(slice.dataset.arc);
    const steps = Math.max(2, Math.ceil(arc / (Math.PI / 48)));
    const center = [pie.left + pie.width / 2, pie.top + pie.height / 2];
    const wedge = [center];
    const strip = [[segment.left, segment.bottom]];
    for (let step = 0; step <= steps; step++) {
      const portion = step / steps;
      const point = angle + arc * portion;
      wedge.push([center[0] + Math.cos(point) * pie.width / 2,
                  center[1] + Math.sin(point) * pie.height / 2]);
      strip.push([segment.left + segment.width * portion, segment.top]);
    }
    wedge.push(wedge[wedge.length - 1]);
    strip.push([segment.right, segment.bottom]);
    const layer = el('div');
    layer.style.backgroundColor = slice.style.backgroundColor;
    overlay.append(layer);
    const frames = [polygon(wedge), polygon(strip)];
    if (expanding) frames.reverse();
    animations.push(layer.animate(frames.map(clipPath => ({clipPath})), timing));
  });
  if (expanding) {
    for (const detail of [$('genre-breakdown'), panel.querySelector('.genre-note')]) {
      animations.push(detail.animate([{opacity: 0}, {opacity: 0, offset: .55}, {opacity: 1}], timing));
    }
  }
  const cleanup = () => {
    for (const animation of animations) animation.cancel();
    overlay.remove();
    target.classList.remove('genre-morph-target');
    if (finishGenreTransition === cleanup) finishGenreTransition = null;
  };
  finishGenreTransition = cleanup;
  animations[0].finished.then(cleanup, () => {});
}

applyGenreLayout();

function renderGenres() {
  const groups=new Map();
  let totalBytes=0;
  for(const book of library.books){
    totalBytes+=book.size;
    const tags=new Map((book.genres||[]).map(label=>[label.toLocaleLowerCase(),label]));
    if(!tags.size)tags.set(untaggedGenre,'Untagged');
    for(const [key,label] of tags){
      if(!groups.has(key))groups.set(key,{label,share:0,bytes:0});
      const group=groups.get(key);
      group.share+=1/tags.size;
      group.bytes+=book.size/tags.size;
    }
  }
  const filter=$('genre-filter');
  const selection=filter.value;
  const optionValue=key=>key===untaggedGenre?'untagged':'genre:'+key;
  filter.replaceChildren(new Option('All genres',''));
  for(const [key,group] of [...groups].sort((a,b)=>genreCollator.compare(a[1].label,b[1].label))){
    filter.add(new Option(group.label,optionValue(key)));
  }
  if([...filter.options].some(option=>option.value===selection))filter.value=selection;

  finishGenreTransition?.();
  const overview=$('genre-overview');
  overview.hidden=!library.connected||!library.books.length;
  $('shelf-overview').hidden=overview.hidden&&$('storage').hidden;
  $('overview-nav').hidden=overview.hidden||$('storage').hidden;
  const breakdown=$('genre-breakdown');
  const legendScroll=breakdown.scrollTop;
  breakdown.replaceChildren();
  const pie=$('genre-pie');
  pie.replaceChildren();
  const bar=$('genre-bar');
  bar.replaceChildren();
  const percent=new Intl.NumberFormat(undefined,{style:'percent',maximumFractionDigits:1});
  const ranked=[...groups].sort((a,b)=>b[1].share-a[1].share||genreCollator.compare(a[1].label,b[1].label));
  let angle=-Math.PI/2;
  ranked.forEach(([key,group],index)=>{
    const color=key===untaggedGenre?'var(--chart-none)':shelfColors[index%shelfColors.length];
    const countShare=group.share/library.books.length;
    const summary=percent.format(countShare)+' of books';
    const activate=()=>{
      if(busy)return;
      filter.value=filter.value===optionValue(key)?'':optionValue(key);
      folder=null;
      $('search').value='';
      render();
      const target=(overviewCollapsed?bar:breakdown).children[index];
      if(target)target.focus({preventScroll:true});
    };
    const selected=filter.value===optionValue(key);
    const label=group.label+' · '+summary;
    const slice=el('button',null,'genre-slice');
    slice.type='button';
    slice.tabIndex=-1;
    slice.setAttribute('aria-label',label);
    slice.setAttribute('aria-pressed',String(selected));
    slice.title=label;
    slice.style.backgroundColor=color;
    const arc=countShare*2*Math.PI;
    if(ranked.length>1){
      const points=['50% 50%'];
      const steps=Math.max(1,Math.ceil(arc/(Math.PI/48)));
      for(let step=0;step<=steps;step++){
        const point=angle+arc*step/steps;
        // Extend the wedge past the circle so its outer edge stays round.
        points.push(`${50+72*Math.cos(point)}% ${50+72*Math.sin(point)}%`);
      }
      slice.style.clipPath='polygon('+points.join(',')+')';
    }
    slice.dataset.angle=angle;
    slice.dataset.arc=arc;
    angle+=arc;
    const segment=el('button',null,'genre-segment');
    segment.type='button';
    segment.style.flexGrow=countShare;
    segment.style.backgroundColor=color;
    segment.title=label;
    segment.setAttribute('aria-label',label);
    segment.setAttribute('aria-pressed',String(selected));
    segment.onclick=activate;
    bar.append(segment);
    slice.onclick=activate;
    pie.append(slice);

    const row=el('button',null,'genre-row');
    row.type='button';
    row.setAttribute('aria-pressed',String(selected));
    const swatch=el('i');
    swatch.style.backgroundColor=color;
    row.append(swatch,el('span',group.label),el('span',summary),
      el('span',percent.format(totalBytes?group.bytes/totalBytes:0)+' of EPUB size'));
    row.onclick=activate;
    breakdown.append(row);
  });
  breakdown.scrollTop=legendScroll;
  return filter.value;
}
const seriesKey = name => (name || '').toLocaleLowerCase();
function seriesLabel(book) {
  return book.seriesPosition ? `${book.series} · #${book.seriesPosition}` : book.series;
}

function renderSeriesFilter() {
  const filter = $('series-filter');
  const selected = filter.value;
  const series = new Map(library.books.filter(book => book.series)
    .map(book => [seriesKey(book.series), book.series]));
  const options = [...series].sort((a, b) => genreCollator.compare(a[1], b[1]));
  filter.replaceChildren(new Option('All series', ''), ...options.map(([key, label]) => new Option(label, key)));
  filter.value = series.has(selected) ? selected : '';
  return filter.value;
}

function compareSeriesPositions(a, b) {
  const valid = /^[0-9]+(?:\.[0-9]+)*$/;
  const parts = value => {
    if (!valid.test(value)) return [];
    const [whole, fraction = '0', ...subpositions] = value.split('.');
    return [Number(whole), Number('0.' + fraction), ...subpositions.map(Number)];
  };
  const left = parts(a);
  const right = parts(b);
  if (!left.length || !right.length) return Number(!left.length) - Number(!right.length);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const difference = (left[i] || 0) - (right[i] || 0);
    if (difference) return difference;
  }
  return 0;
}

// Storage totals walk every file, so they load after the shelf appears.
let storageRequest = null;
function loadStorage(shelf) {
  if (storageRequest?.shelf === shelf) return;
  const request = {shelf};
  storageRequest = request;
  shelfRequest('/api/storage').then(r => r.ok ? r.json() : null).catch(() => null).then(storage => {
    if (storageRequest !== request || library !== shelf) return;
    shelf.storage = storage;
    render();
  });
}

function render() {
  document.body.classList.toggle('waiting',!library.connected);
  renderConnection();
  const welcome=!library.connected&&!ejected&&library.needsLibrary;
  $('waiting-title').textContent=ejected?'Safe travels, Kindle.':welcome?'Welcome to KoShelf':'Waiting for your library';
  $('waiting-message').textContent=ejected?'Your Kindle has been safely ejected. You can unplug it now.':
    welcome?'KoShelf keeps your books in a KoShelf Library folder in your home folder. Create it to get started, or choose another folder.':
    'Plug in your Kindle. We’ll take it from here.';
  $('waiting-status').textContent=ejected?'Safely ejected':welcome?'No library yet':'Looking for your Kindle';
  $('waiting-create').hidden=!welcome;
  document.body.classList.toggle('ejected',ejected);
  $('remove-folder').hidden=!library.connected||!folder;
  $('remove-folder').disabled=busy||loading;
  $('new-folder').disabled=!library.connected || busy || loading;
  $('undo').disabled=busy || !library.connected;
  $('status').className=library.connected?'connected':'';
  $('status').replaceChildren(el('i'),document.createTextNode(library.connected?(library.root===library.macRoot?'Mac library':library.root===device.kindle?'Kindle library':'Library connected'):'Library unavailable'));
  $('library-label').textContent=library.root===library.macRoot?'MAC LIBRARY':library.canEject||library.root===device.kindle?'KINDLE LIBRARY':'YOUR LIBRARY';
  $('count').textContent=library.books.length;
  $('all').classList.toggle('active', folder === null);
  $('heading').textContent=folder===null?'Your bookshelf':folder===''?'Books in root':folder.split('/').pop();
  $('subtitle').textContent=library.connected?`${library.books.length} EPUB${library.books.length===1?'':'s'} · A little space to explore your library.`:'Connect your Kindle, or choose a folder to begin.';
  $('root').textContent=library.root||'No folder selected';
  if (library.connected && library.storage === undefined) loadStorage(library);
  renderStorage();
  $('folders').replaceChildren();
  for(const f of ['',...library.folders]) {
    const button = el('button', null, 'nav folder' + (folder === f ? ' active' : ''));
    const icon = el('i', null, f === '' ? 'root-icon' : 'folder-shelf-icon');
    icon.setAttribute('aria-hidden', 'true');
    button.append(icon, el('span', f || 'Books in root', 'folder-name'));
    const stat = library.storage?.folders?.[f];
    const size = stat ? formatBytes(f === '' ? stat.direct : stat.bytes) : '';
    if (size) button.append(el('small', size, 'folder-size'));
    button.title = (f || 'Files directly in library root') + (size ? ' · ' + size : '');
    button.onclick = () => { if (!busy) { folder = f; render(); } };
    dropTarget(button, f);
    $('folders').append(button);
  }
  const q=$('search').value.toLocaleLowerCase().trim();
  const genre=renderGenres();
  const series=renderSeriesFilter();
  const reading=$('reading-filter').value;
  const books=library.books.filter(book=>{
    const tags=book.genres||[];
    const matchesGenre=!genre||(genre==='untagged'?!tags.length:tags.some(tag=>'genre:'+tag.toLocaleLowerCase()===genre));
    return (folder===null||book.folder===folder)&&matchesGenre&&
      (!series || seriesKey(book.series) === series)&&
      (!reading || reading === (book.readingStatus || 'unknown'))&&
      [book.title,book.author,book.filename,book.path,book.series||'',...tags].some(value=>value.toLocaleLowerCase().includes(q));
  });
  const sort=$('sort').value;
  books.sort((a,b)=>{
    if(sort==='modified')return b.modified-a.modified;
    if(sort==='series'){
      return Number(!a.series)-Number(!b.series)||
        genreCollator.compare(seriesKey(a.series),seriesKey(b.series))||
        compareSeriesPositions(a.seriesPosition,b.seriesPosition)||
        genreCollator.compare(a.title,b.title);
    }
    if(sort==='genre'){
      const first=book=>book.genres?.[0];
      return Number(!first(a))-Number(!first(b))||
        genreCollator.compare(first(a)||'',first(b)||'')||genreCollator.compare(a.title,b.title);
    }
    return genreCollator.compare(a[sort],b[sort]);
  });
  if (selectionRoot !== library.root || !library.connected) {
    selectionRoot = library.root;
    selectionMode = false;
    selectedBooks.clear();
  }
  const available = new Set(library.books.map(book => book.path));
  for (const path of selectedBooks) if (!available.has(path)) selectedBooks.delete(path);
  visibleBooks = books;
  renderSelection();
  $('results').textContent=`${books.length} book${books.length===1?'':'s'}`;
  coverObserver.disconnect();
  const cards = books.map(bookCard);
  const grid = $('grid');
  if (cards.length !== grid.children.length || cards.some((entry, index) => grid.children[index] !== entry.card)) {
    // Reusing existing cards keeps covers loaded and avoids rebuilding the shelf on every keystroke.
    grid.replaceChildren(...cards.map(entry => entry.card));
  }
  for (const entry of cards) {
    entry.card.draggable = !selectionMode && !busy && library.connected;
    entry.update?.();
  }
  if (cardCache.size > library.books.length + 200) {
    for (const [path, entry] of cardCache) if (!available.has(path)) { releaseCard(entry); cardCache.delete(path); }
  }
  for (const {art, book} of cards) {
    if (!book.hasCover || art.querySelector('img')?.style.visibility === '') continue;
    const bounds = art.getBoundingClientRect();
    if (bounds.bottom >= 0 && bounds.top <= innerHeight + 200) {
      void loadCover(art.querySelector('img'));
    } else {
      coverObserver.observe(art);
    }
  }
  $('empty').hidden=books.length>0;
  if(!books.length) {
    $('empty').querySelector('h2').textContent=library.connected?'No books here yet.':'A home for your next read.';
    $('empty').querySelector('p').textContent=library.connected?(q||genre||series||reading?'Try a different search or filter.':'This view contains no EPUB files. Choose another folder or browse all books.'):'Connect your Kindle and choose its books folder. You can also explore a local EPUB folder.';
    $('connect').hidden=library.connected;
  }
}
const cardCache = new Map();

function cardKey(book) {
  return [library.root, selectionMode, book.title, book.author, book.folder, book.series, book.seriesPosition,
    book.readingStatus, book.readingProgress, book.readingStatusPending, book.booksProgress, book.modified,
    book.hasCover].join('\u0000');
}

function releaseCard(entry) {
  const image = entry.art.querySelector('img');
  if (image?.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
}

function bookCard(book) {
  const key = cardKey(book);
  const cached = cardCache.get(book.path);
  if (cached && cached.key === key) {
    cached.book = book;
    return cached;
  }
  if (cached) releaseCard(cached);
  const card = el('button', null, 'book');
  const art = cover(book, true);
  const entry = {key, card, art, book};
  card.append(art, el('h3', book.title), el('div', book.author, 'author'), el('div', book.folder || 'EPUB', 'book-path'));
  if (book.series) card.append(el('div', seriesLabel(book), 'book-series'));
  if (book.readingStatus) card.append(readingProgress(book));
  if (book.booksProgress != null) card.append(el('div', `Apple Books · ${percentLabel(book.booksProgress)}`, 'books-progress'));
  if (selectionMode) {
    const check = el('span', null, 'book-check');
    check.setAttribute('aria-hidden', 'true');
    art.append(check);
    entry.update = () => {
      const selected = selectedBooks.has(entry.book.path);
      card.setAttribute('aria-pressed', String(selected));
      check.textContent = selected ? '✓' : '';
    };
    card.onclick = () => {
      if (busy) return;
      if (selectedBooks.has(entry.book.path)) selectedBooks.delete(entry.book.path);
      else selectedBooks.add(entry.book.path);
      entry.update();
      renderSelection();
    };
  } else {
    card.onclick = () => { if (!busy) details(entry.book); };
    card.title = 'Drag to a folder, or click for details';
    card.addEventListener('dragstart', event => {
      if (busy) { event.preventDefault(); return; }
      dragged = entry.book;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('application/x-kindle-shelf', entry.book.path);
      card.classList.add('dragging');
      document.body.classList.add('drag-active');
    });
    card.addEventListener('dragend', () => {
      dragged = null;
      document.body.classList.remove('drag-active');
      document.querySelectorAll('.drop-over,.dragging').forEach(node => node.classList.remove('drop-over', 'dragging'));
    });
  }
  cardCache.set(book.path, entry);
  return entry;
}

const readingLabels = {reading: 'Reading', complete: 'Finished', abandoned: 'On hold'};

function readingProgress(book, showLabel = true) {
  const status = el('div', null, 'book-reading');
  status.dataset.status = book.readingStatus;
  const label = readingLabels[book.readingStatus] || 'No saved status';
  const fraction = book.readingStatus === 'complete' ? 1 : book.readingProgress;
  if (showLabel) status.append(el('span', label));
  if (fraction == null) return status;

  const percent = fraction * 100;
  const display = percent > 0 && percent < 1 ? '<1%' : `${Math.min(fraction < 1 ? 99 : 100, Math.round(percent))}%`;
  const track = el('span', null, 'book-reading-track');
  track.setAttribute('aria-hidden', 'true');
  const fill = el('span', null, 'book-reading-fill');
  fill.style.width = `${percent}%`;
  if (percent > 0) fill.style.minWidth = '2px';
  track.append(fill);
  status.append(track, el('span', display, 'book-reading-percent'));
  return status;
}

function renderDuplicateGroups(results) {
  const content = $('duplicates-results');
  const shelf = new Map(library.books.map(item => [item.path, item]));
  for (const [groups, title, description] of [
    [results.exact, 'Exact copies', 'These EPUB files are identical.'],
    [results.possible, 'Possible matches', 'The titles and authors match. These may be different editions or versions.']
  ]) {
    if (!groups.length) continue;
    const section = el('section');
    section.append(el('h3', `${title} (${groups.length})`, 'eyebrow'), el('p', description));
    for (const books of groups) {
      const card = el('article', null, 'duplicate-card');
      card.append(el('h4', books[0].title), el('p', books[0].author));
      const copies = el('ul');
      for (const book of books) {
        const row = el('li');
        const path = el('span', book.path);
        const action = el('button', 'Book details', 'button');
        const current = shelf.get(book.path);
        action.type = 'button';
        action.disabled = !current;
        action.setAttribute('aria-label', `Book details for ${book.path}`);
        action.onclick = () => {
          $('duplicates-dialog').close();
          details(current);
        };
        const keep = el('button', 'Keep this copy', 'button primary');
        keep.type = 'button';
        keep.setAttribute('aria-label', `Keep ${book.path} and archive other copies`);
        keep.onclick = () => confirmDuplicateArchive(book, books.filter(other => other !== book));
        const actions = el('div', null, 'duplicate-actions');
        actions.append(action, keep);
        row.append(path, el('small', formatBytes(book.size)), actions);
        copies.append(row);
      }
      card.append(copies);
      section.append(card);
    }
    content.append(section);
  }
}

async function scanDuplicates(message = '') {
  const dialog = $('duplicates-dialog');
  const status = $('duplicates-status');
  const results = $('duplicates-results');
  const root = library.root;
  results.replaceChildren();
  status.textContent = 'Checking EPUBs on this shelf…';
  setBusy(true);
  if (!dialog.open) dialog.showModal();
  try {
    const response = await shelfRequest('/api/duplicates?' + new URLSearchParams({root}));
    const report = await response.json();
    if (!response.ok) throw Error(report.error || 'Could not check for duplicates.');
    if (!dialog.open) return;
    renderDuplicateGroups(report);
    status.textContent = report.exact.length || report.possible.length
      ? message : [message, 'No duplicates found on this shelf.'].filter(Boolean).join(' ');
  } catch (error) {
    status.textContent = error.message;
  } finally {
    setBusy(false);
  }
};

$('find-duplicates').onclick = () => {
  if (!busy && !loading && library.connected) scanDuplicates();
};

async function confirmDuplicateArchive(keeper, others) {
  if (busy || !library.connected) return;
  const dialog = $('archive-confirm');
  $('archive-keeper').textContent = `Keep ${keeper.path} on this shelf.`;
  $('archive-candidates').replaceChildren(...others.map(book => el('li', book.path)));
  dialog.returnValue = 'cancel';
  const confirmed = new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'archive'), {once: true});
  });
  dialog.showModal();
  if (!await confirmed) return;

  const root = library.root;
  const status = $('duplicates-status');
  $('duplicates-results').replaceChildren();
  status.textContent = 'Verifying and archiving EPUBs…';
  setBusy(true);
  let count = 0;
  const errors = [];
  for (const book of others) {
    try {
      await mutation('/api/duplicate-archive', {keep: keeper.path, remove: book.path}, {root, updateStorage: false});
      count++;
    } catch (error) {
      errors.push(`${book.path} · ${error.message}`);
    }
  }
  try { await readLibrary(); }
  catch (error) { errors.push(error.message); }
  finally { setBusy(false); render(); }
  const summary = [`${count} ${count === 1 ? 'EPUB' : 'EPUBs'} archived.`, ...errors].join(' ');
  $('notice').textContent = summary;
  await scanDuplicates(summary);
}

$('open-duplicate-archive').onclick = async () => {
  const dialog = $('duplicate-archive-dialog');
  const status = $('duplicate-archive-status');
  const entries = $('duplicate-archive-entries');
  entries.replaceChildren();
  status.textContent = 'Loading archive…';
  dialog.showModal();
  try {
    const response = await shelfRequest('/api/duplicate-archive');
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Could not read the duplicate archive.');
    status.textContent = data.length ? 'Saved locally on this Mac.' : 'The duplicate archive is empty.';
    for (const entry of data) {
      const card = el('article', null, 'duplicate-card');
      card.append(el('h4', entry.title), el('p', 'Original location · ' + entry.root + '/' + entry.path),
        el('p', 'Archived EPUB · ' + entry.archivePath));
      entries.append(card);
    }
  } catch (error) {
    status.textContent = error.message;
  }
};

async function loadSyncHistory(expandedRun) {
  const status = $('sync-history-status');
  const runs = $('sync-history-runs');
  status.textContent = 'Loading sync history…';
  try {
    const response = await shelfRequest('/api/sync-history');
    const history = await response.json();
    if (!response.ok) throw Error(history.error || 'Could not load sync history.');
    runs.replaceChildren();
    status.textContent = history.length ? '' : 'Completed EPUB changes will appear here after your next sync.';
    for (const run of history) {
      const card = el('details', null, 'duplicate-card');
      card.open = run.id === expandedRun;
      const date = new Date(run.time * 1000).toLocaleString();
      card.append(el('summary', `${date} · ${run.mode} · ${run.status} · ${run.actions.length} changes`));
      for (const message of run.errors || []) card.append(el('p', message));
      for (const action of run.actions) {
        const row = el('div', null, 'shelf-report-row');
        const labels = {copied:'Copied', replaced:'Replaced', archived:'Archived', moved:'Moved', reading:'Updated reading progress', 'saved version':'Saved older version', 'kept version':'Kept the Kindle version in KoShelf’s recovery folder'};
        const name = action.origin ? `${action.origin} → ${action.path}` : action.path;
        const description = el('div');
        description.append(el('strong', name), el('small', `${action.side} · ${labels[action.kind]}${action.undone ? ' · Undone' : ''}`));
        row.append(description);
        if (!action.undone && action.kind !== 'kept version') {
          const undo = el('button', 'Undo…', 'button');
          undo.disabled = action.side === 'Kindle' && !device.kindle;
          undo.onclick = () => {
            undo.replaceWith(el('span', 'Undo this change?'), confirm, cancel);
            confirm.focus();
          };
          const confirm = el('button', 'Confirm undo', 'button primary');
          const cancel = el('button', 'Cancel', 'button');
          cancel.onclick = () => {
            row.replaceChildren(description, undo);
            undo.focus();
          };
          confirm.onclick = async () => {
            if (busy) return;
            confirm.disabled = cancel.disabled = $('close-sync-history').disabled = true;
            setBusy(true);
            status.textContent = 'Restoring book…';
            try {
              await mutation('/api/sync-undo', {run:run.id, action:action.id}, {updateStorage:false});
              await loadSyncHistory(run.id);
              status.textContent = 'Sync change undone.';
              await readLibrary();
              render();
              comparedAt = 0;
            } catch (error) {
              status.textContent = error.message;
            } finally {
              confirm.disabled = cancel.disabled = $('close-sync-history').disabled = false;
              setBusy(false);
              checkConnection().catch(() => {});
            }
          };
          row.append(undo);
        }
        card.append(row);
      }
      runs.append(card);
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

$('open-sync-history').onclick = () => {
  $('shelf-actions').open = false;
  $('sync-history-runs').replaceChildren();
  $('sync-history-dialog').showModal();
  loadSyncHistory();
};
$('sync-history-dialog').addEventListener('cancel', event => {
  if (busy) event.preventDefault();
});

const cloudDialog = $('icloud-dialog');
let cloudSession = null;

async function cloudGet(endpoint) {
  const response = await shelfRequest(endpoint);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Could not read iCloud sync information.');
  return data;
}

function renderCloudFolder(config, configuring = !config.path) {
  $('icloud-config').hidden = !configuring;
  $('icloud-connected').hidden = configuring;
  $('icloud-keep-folder').hidden = !config.path;
  $('icloud-preview').hidden = configuring || !config.available;
  $('icloud-folder-path').textContent = config.path || '';
  $('icloud-folder-state').textContent = config.available ? 'Folder connected' : 'Folder unavailable';
  $('icloud-path').value = config.path || '';
  $('icloud-path').placeholder = config.defaultPath || 'Paste an iCloud Drive folder path';
  $('icloud-disconnect-confirm').hidden = true;
}

function clearCloudReview() {
  cloudSession.review = null;
  $('icloud-review').replaceChildren();
  $('icloud-apply').hidden = true;
  $('icloud-delete-help').hidden = true;
}

function cloudSide(side) {
  return side === 'mac' ? 'Mac' : 'iCloud Drive';
}

function cloudActionLabel(action) {
  const target = cloudSide(action.target);
  if (action.moveTo) return `Move to ${action.moveTo} on ${target}`;
  if (action.moveFrom) return `New location for ${action.moveFrom} on ${target}`;
  switch (action.kind) {
    case 'copy': return `Copy to ${target}`;
    case 'replace': return `Update on ${target}`;
    case 'delete': return `Remove from ${target}`;
    default: return 'Review conflicting changes';
  }
}

function appendCloudErrors(container, errors = []) {
  for (const {path, error, recovery} of errors) {
    const entry = el('p', `${path} · ${error}`);
    if (recovery) entry.append(el('small', `Recovery copy: ${recovery}`));
    container.append(entry);
  }
}

function renderCloudReview(review) {
  const list = $('icloud-review');
  const choices = Object.create(null);
  const actionable = new Set();
  for (const action of review.actions) {
    const row = el('div', null, 'shelf-report-row');
    const label = el('div');
    label.append(el('strong', action.path), el('small', cloudActionLabel(action)));
    row.append(label);
    if (action.needsReview || action.kind === 'delete' || action.kind === 'conflict') {
      const select = el('select', null, 'field');
      select.setAttribute('aria-label', `Resolution for ${action.path}`);
      const options = [['skip', 'Skip for now']];
      if (action.kind === 'delete') {
        options.push([action.source || (action.target === 'mac' ? 'icloud' : 'mac'), cloudActionLabel(action)]);
        $('icloud-delete-help').hidden = false;
      } else {
        for (const side of ['mac', 'icloud']) {
          if (action[side]) options.push([side, `Keep ${cloudSide(side)} ${action[side] === 'folder' ? 'folder' : 'EPUB'}`]);
          else options.push([side, `Match removal on ${cloudSide(side)}`]);
        }
        $('icloud-delete-help').hidden = false;
      }
      for (const [value, title] of options) {
        const option = el('option', title);
        option.value = value;
        select.append(option);
      }
      choices[action.path] = 'skip';
      select.disabled = options.length === 1;
      select.onchange = () => {
        choices[action.path] = select.value;
        if (select.value === 'skip') actionable.delete(action.path);
        else actionable.add(action.path);
        $('icloud-apply').disabled = !actionable.size;
      };
      row.append(select);
    } else if (!action.moveFrom) actionable.add(action.path);
    list.append(row);
  }
  cloudSession.review = {token: review.token, choices};
  $('icloud-status').textContent = review.actions.length ?
    `${review.actions.length} ${review.actions.length === 1 ? 'change' : 'changes'} to review.` :
    'Your Mac library and iCloud Drive folder match.';
  $('icloud-apply').hidden = !review.actions.length;
  $('icloud-apply').disabled = !actionable.size;
}

async function runCloudTask(message, task) {
  const session = cloudSession;
  if (session.pending || busy || loading) return;
  session.pending = true;
  setBusy(true);
  $('icloud-status').textContent = message;
  $('icloud-progress').hidden = false;
  $('icloud-config').inert = true;
  $('icloud-connected').inert = true;
  $('icloud-review').inert = true;
  for (const id of ['icloud-close', 'icloud-preview', 'icloud-apply']) $(id).disabled = true;
  const stopProgress = watchProgress((label, done, total) => {
    if (!total) return;
    $('icloud-progress').max = total;
    $('icloud-progress').value = done;
    $('icloud-status').textContent = `${label} · ${Math.min(done + 1, total)} of ${total}`;
  });
  try {
    await task(session);
  } catch (error) {
    $('icloud-status').textContent = error.message;
  } finally {
    stopProgress();
    $('icloud-progress').removeAttribute('value');
    session.pending = false;
    setBusy(false);
    $('icloud-progress').hidden = true;
    $('icloud-config').inert = false;
    $('icloud-connected').inert = false;
    $('icloud-review').inert = false;
    $('icloud-close').disabled = false;
    $('icloud-preview').disabled = false;
  }
}

$('open-icloud-sync').onclick = () => {
  if (busy || loading || !library.connected || library.root !== library.macRoot) return;
  $('shelf-actions').open = false;
  cloudSession = {root: library.root, config: null, review: null, pending: false};
  $('icloud-config').hidden = true;
  $('icloud-connected').hidden = true;
  $('icloud-preview').hidden = true;
  $('icloud-history').open = false;
  $('icloud-history-runs').replaceChildren();
  $('icloud-pick').hidden = !window.webkit?.messageHandlers?.chooseICloudFolder;
  clearCloudReview();
  cloudDialog.showModal();
  runCloudTask('Checking iCloud Drive folder…', async session => {
    session.config = await cloudGet('/api/icloud-status');
    renderCloudFolder(session.config);
    $('icloud-status').textContent = session.config.path && !session.config.available ?
      session.config.reason || 'Open the folder in Finder to check that it’s available.' : '';
  });
};

cloudDialog.addEventListener('cancel', event => {
  if (cloudSession?.pending) event.preventDefault();
});

$('icloud-pick').onclick = () => window.webkit.messageHandlers.chooseICloudFolder.postMessage(null);
window.onICloudFolderChosen = path => {
  if (cloudDialog.open && typeof path === 'string') $('icloud-path').value = path;
};

$('icloud-change').onclick = () => {
  clearCloudReview();
  renderCloudFolder(cloudSession.config, true);
  $('icloud-status').textContent = 'A new folder starts a separate sync comparison.';
  $('icloud-path').focus();
};
$('icloud-keep-folder').onclick = () => {
  const {config} = cloudSession;
  renderCloudFolder(config);
  $('icloud-status').textContent = config.reason || '';
  $('icloud-change').focus();
};

$('icloud-setup').onsubmit = event => {
  event.preventDefault();
  const path = $('icloud-path').value.trim();
  if (!path) return;
  updateCloudFolder(path);
};

$('icloud-disconnect').onclick = () => {
  clearCloudReview();
  $('icloud-disconnect-confirm').hidden = false;
  $('icloud-preview').hidden = true;
  $('icloud-disconnect-cancel').focus();
};
$('icloud-disconnect-cancel').onclick = () => {
  $('icloud-disconnect-confirm').hidden = true;
  $('icloud-preview').hidden = !cloudSession.config.available;
  $('icloud-disconnect').focus();
};
$('icloud-disconnect-confirmed').onclick = () => updateCloudFolder(null);

function updateCloudFolder(path) {
  runCloudTask(path ? 'Connecting iCloud Drive folder…' : 'Disconnecting iCloud sync…', async session => {
    session.config = await mutation('/api/icloud-setup', {path}, {updateStorage: false, root: session.root});
    clearCloudReview();
    renderCloudFolder(session.config);
    $('icloud-status').textContent = path ? 'Folder connected. Review changes to start your first sync.' : 'iCloud sync disconnected.';
  });
}

$('icloud-preview').onclick = () => {
  clearCloudReview();
  runCloudTask('Comparing EPUBs on your Mac and in iCloud Drive…', async session => {
    const review = await mutation('/api/icloud-sync', {}, {updateStorage: false, root: session.root});
    renderCloudReview(review);
  });
};

$('icloud-apply').onclick = async () => {
  const review = cloudSession.review;
  if (!review) return;
  clearCloudReview();
  await runCloudTask('Syncing EPUBs with your iCloud Drive folder…', async session => {
    const result = await mutation('/api/icloud-sync', review, {updateStorage: false, root: session.root});
    const counts = [`${result.applied} applied`, `${result.skipped} skipped`];
    if (result.errors?.length) counts.push(`${result.errors.length} failed`);
    $('icloud-status').textContent = counts.join(' · ') + '. Check Finder for upload status.' +
      (result.warning ? ` ${result.warning}` : '');
    appendCloudErrors($('icloud-review'), result.errors);
  });
  if ($('icloud-history').open) loadCloudHistory();
  await refresh();
};

async function loadCloudHistory() {
  if (!$('icloud-history').open) return;
  const session = cloudSession;
  const list = $('icloud-history-runs');
  list.replaceChildren(el('p', 'Loading recent syncs…'));
  try {
    const data = await cloudGet('/api/icloud-history');
    if (session !== cloudSession || !cloudDialog.open) return;
    const runs = Array.isArray(data) ? data : data.runs;
    list.replaceChildren();
    for (const run of runs) {
      const details = el('details', null, 'duplicate-card');
      const date = new Date(typeof run.timestamp === 'number' ? run.timestamp * 1000 : run.timestamp);
      const status = run.status?.startsWith('Completed') ? `${run.applied} applied · ${run.skipped} skipped` +
        (run.errors?.length ? ` · ${run.errors.length} failed` : '') :
        [run.status, run.error].filter(Boolean).join(' · ');
      details.append(el('summary', `${date.toLocaleString()} · ${status}`));
      if (run.path) details.append(el('p', run.path));
      for (const action of run.actions || []) {
        details.append(el('small', `${cloudActionLabel(action)} · ${action.path}`));
        if (action.recovery) details.append(el('small', `Recovery copy: ${action.recovery}`));
      }
      appendCloudErrors(details, run.errors);
      list.append(details);
    }
    if (!list.firstChild) list.append(el('p', 'No iCloud syncs recorded yet.'));
  } catch (error) {
    if (session === cloudSession) list.replaceChildren(el('p', error.message));
  }
}
$('icloud-history').addEventListener('toggle', loadCloudHistory);

function healthIssues(books) {
  const readable = books.filter(book => !book.warning);
  const aliases = new Map();
  for (const book of readable) {
    if (!book.series) continue;
    const key = book.series.normalize('NFKC').toLocaleLowerCase().replace(/\p{P}|\s/gu, '');
    if (!aliases.has(key)) aliases.set(key, new Set());
    aliases.get(key).add(book.series);
  }
  const inconsistent = new Set([...aliases.values()].filter(names => names.size > 1).flatMap(names => [...names]));
  return [
    ['Unreadable EPUB metadata', books.filter(book => book.warning)],
    ['Missing covers', readable.filter(book => !book.hasCover)],
    ['Missing authors', readable.filter(book => !book.authors?.length)],
    ['Missing genres', readable.filter(book => !book.genres?.length)],
    ['Series without a position', readable.filter(book => book.series && !book.seriesPosition)],
    ['Possible series name variants', readable.filter(book => inconsistent.has(book.series))],
  ];
}

$('open-library-health').onclick = async () => {
  if (busy || loading || !library.connected) return;
  $('shelf-actions').open = false;
  const dialog = $('library-health-dialog');
  const status = $('library-health-status');
  const results = $('library-health-results');
  results.replaceChildren();
  status.textContent = 'Checking your library…';
  dialog.showModal();
  try {
    const root = library.root;
    const response = await shelfRequest('/api/library');
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Could not check the library.');
    if (!data.connected || data.root !== root || library.root !== root) throw Error('The library changed. Run the check again.');
    const groups = healthIssues(data.books).filter(([, books]) => books.length);
    const affected = new Set(groups.flatMap(([, books]) => books.map(book => book.path))).size;
    status.textContent = affected ? `${affected} of ${data.books.length} books have findings to review.` :
      `Checked ${data.books.length} books. No issues found.`;
    for (const [label, books] of groups) {
      const group = el('details', null, 'duplicate-card');
      group.append(el('summary', `${label} · ${books.length}`));
      for (const book of books) {
        const button = el('button', null, 'button shelf-health-book');
        button.append(el('strong', book.title), el('small', book.series ? `${book.series} · ${book.path}` : book.path));
        button.onclick = async () => {
          dialog.close();
          if (library.root !== root) return;
          await refresh();
          const current = library.root === root && library.books.find(entry => entry.path === book.path);
          if (current) details(current);
        };
        group.append(button);
      }
      results.append(group);
    }
  } catch (error) {
    status.textContent = error.message;
  }
};

let leftoverReview = null;

function renderSyncLeftovers(preview) {
  const results = $('sync-leftovers-results');
  const names = {mac: 'Mac library', icloud: 'iCloud Drive folder'};
  const groups = [
    ['appledouble', 'Hidden ._ files copied from the Kindle'],
    ['backup', 'Backup copies next to their original'],
  ];
  results.replaceChildren();
  for (const [kind, label] of groups) {
    for (const key of Object.keys(preview.libraries)) {
      const items = preview.items.filter(item => item.kind === kind && item.library === key);
      if (!items.length) continue;
      const group = el('details', null, 'duplicate-card');
      group.append(el('summary', `${label} · ${names[key] || key} · ${items.length}`));
      const list = el('ul', null, 'transfer-paths');
      list.append(...items.slice(0, 200).map(item => el('li', item.path)));
      if (items.length > 200) list.append(el('li', `And ${items.length - 200} more`));
      group.append(list);
      results.append(group);
    }
  }
  if (preview.kept.length) {
    const group = el('details', null, 'duplicate-card');
    group.append(el('summary', `Left in place because the original is missing · ${preview.kept.length}`),
      el('p', 'These copies may be the only copy of a book, so KoShelf leaves them on your shelf.'));
    const list = el('ul', null, 'transfer-paths');
    list.append(...preview.kept.map(item => el('li', item.path)));
    group.append(list);
    results.append(group);
  }
}

async function previewSyncLeftovers() {
  const status = $('sync-leftovers-status');
  const apply = $('apply-sync-leftovers');
  apply.hidden = true;
  leftoverReview = null;
  $('sync-leftovers-results').replaceChildren();
  status.textContent = 'Looking for leftovers…';
  try {
    const preview = await mutation('/api/sync-leftovers', {}, {updateStorage: false});
    leftoverReview = preview.review;
    renderSyncLeftovers(preview);
    status.textContent = preview.items.length
      ? `${preview.items.length} files to move. Review the lists, then move them.`
      : 'No sync leftovers found.';
    apply.hidden = !preview.items.length;
  } catch (error) {
    status.textContent = error.message;
  }
}

$('open-sync-leftovers').onclick = () => {
  if (busy || loading || !library.connected || library.root !== library.macRoot) return;
  $('shelf-actions').open = false;
  $('sync-leftovers-dialog').showModal();
  void previewSyncLeftovers();
};

$('apply-sync-leftovers').onclick = async () => {
  if (!leftoverReview || busy) return;
  const status = $('sync-leftovers-status');
  const apply = $('apply-sync-leftovers');
  apply.disabled = true;
  setBusy(true);
  status.textContent = 'Moving files to the recovery folder…';
  try {
    const result = await mutation('/api/sync-leftovers', {review: leftoverReview});
    leftoverReview = null;
    apply.hidden = true;
    $('sync-leftovers-results').replaceChildren(...result.errors.map(message => el('p', message)));
    status.textContent = result.failed
      ? `Moved ${result.moved} files. ${result.failed} could not be moved and were left in place.`
      : `Moved ${result.moved} files to KoShelf’s recovery folder.`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    apply.disabled = false;
    setBusy(false);
    render();
  }
};

const cleanupDialog = $('genre-cleanup-dialog');
let cleanupSession = null;

function renderCleanupTags() {
  const session = cleanupSession;
  const query = $('cleanup-search').value.trim().toLocaleLowerCase();
  const list = $('cleanup-tags');
  list.replaceChildren();
  for (const {key, tag, count, replacement} of session.preview.tags) {
    if (query && !tag.toLocaleLowerCase().includes(query)) continue;
    const row = el('div', null, 'cleanup-tag');
    const label = el('div', tag, 'cleanup-tag-name');
    const meta = el('small', count === 1 ? '1 book' : `${count} books`);
    label.append(meta);
    const input = el('input', null, 'field');
    input.maxLength = 1000;
    input.setAttribute('list', 'cleanup-genres');
    input.setAttribute('aria-label', `Genre for ${tag}`);
    input.placeholder = 'Enter a genre';
    const action = el('select', null, 'field');
    action.setAttribute('aria-label', `Rule for ${tag}`);
    for (const [value, title] of [['default', 'Use default'], ['map', 'Map to genre'], ['keep', 'Recognize as its own genre'], ['remove', 'Remove tag']]) {
      const option = el('option', title);
      option.value = value;
      action.append(option);
    }
    const override = session.draft[key];
    const mode = session.modes.get(key) || (override === undefined ? 'default' : override === '' ? 'remove' : override === tag ? 'keep' : 'map');
    action.value = mode;
    input.value = mode === 'map' ? override ?? '' : '';
    input.hidden = mode !== 'map';
    const rule = el('small', null, 'cleanup-tag-rule');
    rule.textContent = mode === 'default' && !Object.hasOwn(session.preview.mappings, key) ?
      (replacement === null ? 'Not recognized · left unchanged' : replacement === '' ? 'Default · removes tag' :
        replacement === tag ? 'Default · keeps tag' : `Default · ${replacement}`) : '';
    rule.hidden = !rule.textContent;
    // Show what is saved for this tag, and whether an edit is still unsaved.
    const badge = el('span', null, 'cleanup-tag-badge');
    label.append(badge);
    const paintBadge = () => {
      const saved = session.preview.savedMappings[key];
      const unsaved = (session.modes.has(key) || Object.hasOwn(session.preview.mappings, key)) && session.draft[key] !== saved;
      badge.classList.toggle('unsaved', unsaved);
      badge.textContent = unsaved ? 'Unsaved change' : saved === undefined ? '' :
        saved === '' ? 'Saved · removed' : saved === tag ? 'Saved · recognized' : `Saved · ${saved}`;
      badge.hidden = !badge.textContent;
    };
    paintBadge();
    function updateDraft() {
      session.dirty = true;
      session.stale = true;
      updateCleanupApply();
      session.modes.set(key, action.value);
      if (action.value === 'default') delete session.draft[key];
      else if (action.value === 'keep') session.draft[key] = tag;
      else if (action.value === 'remove') session.draft[key] = '';
      else session.draft[key] = input.value.trim();
      $('cleanup-rules-status').textContent = 'Update the preview to see your edits, or save them for future scans.';
      paintBadge();
    }
    action.onchange = () => {
      if (action.value === 'map' && !input.value) input.value = replacement ?? '';
      input.hidden = action.value !== 'map';
      rule.hidden = action.value !== mode || !rule.textContent;
      updateDraft();
      if (!input.hidden) input.focus();
    };
    input.oninput = updateDraft;
    row.append(label, action, input, rule);
    list.append(row);
  }
  if (!list.firstChild) list.append(el('p', query ? 'No tags match your filter.' : 'No embedded tags in this scope.'));
}

function savedMappingNotice(before, preview) {
  const after = preview.savedMappings;
  const labels = new Map(preview.tags.map(tag => [tag.key, tag.tag]));
  const describe = key => {
    const tag = labels.get(key) || key;
    const target = after[key];
    if (target === undefined) return `${tag} · back to default`;
    return target === '' ? `${tag} · removed` : target === tag ? `${tag} · recognized` : `${tag} → ${target}`;
  };
  const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(key => before[key] !== after[key]).sort();
  const books = preview.books.filter(book => book.changes.some(change => change.reason === 'Uses your saved tag mapping.')).length;
  return {changed: changed.map(describe), books};
}

function showSavedNotice(notice) {
  const box = $('cleanup-saved');
  box.hidden = !notice;
  if (!notice) return;
  const {changed, books} = notice;
  const heading = changed.length
    ? `Saved ${changed.length} tag ${changed.length === 1 ? 'mapping' : 'mappings'}`
    : 'Tag mappings saved · nothing changed';
  const list = el('ul');
  list.replaceChildren(...changed.slice(0, 8).map(text => el('li', text)));
  if (changed.length > 8) list.append(el('li', `And ${changed.length - 8} more`));
  const next = books
    ? `${books} ${books === 1 ? 'book now has' : 'books now have'} suggested changes from your mappings below. Books change only when you choose those changes and apply them.`
    : 'No books in this scan need a change from your mappings. Future scans will use them.';
  box.replaceChildren(el('strong', `✓ ${heading}`), list, el('p', next));
}

function renderUnrecognizedTags() {
  const session = cleanupSession;
  const {preview} = session;
  // Unrecognized means KoShelf doesn't know the tag and no mapping for it is saved.
  const tags = preview.tags.filter(tag => !tag.known && !Object.hasOwn(preview.savedMappings, tag.key));
  const section = $('cleanup-unrecognized');
  section.hidden = !tags.length;
  if (!tags.length) return;
  const books = preview.unmappedBooks;
  $('cleanup-unrecognized-heading').textContent =
    `Tags KoShelf doesn't recognize · ${tags.length} ${tags.length === 1 ? 'tag' : 'tags'} on ${books} ${books === 1 ? 'book' : 'books'}`;
  const list = $('cleanup-unrecognized-tags');
  list.replaceChildren(...tags.map(({key, tag, count}) => {
    const row = el('div', null, 'cleanup-unrecognized-tag');
    const name = el('div', tag, 'cleanup-tag-name');
    name.append(el('small', count === 1 ? '1 book' : `${count} books`));
    const recognize = el('button', 'Recognize', 'button');
    recognize.type = 'button';
    recognize.setAttribute('aria-label', `Recognize ${tag} as a genre`);
    recognize.onclick = () => {
      session.draft[key] = tag;
      session.modes.set(key, 'keep');
      if (session.dirty) {
        // Keep the other unsaved edits; they are saved together.
        session.stale = true;
        updateCleanupApply();
        renderCleanupTags();
        $('cleanup-unrecognized-status').textContent = `${tag} will be recognized when you save your tag mappings.`;
        $('cleanup-rules').open = true;
        return;
      }
      session.recognized = tag;
      scanGenres(true);
    };
    const more = el('button', 'Other options…', 'button');
    more.type = 'button';
    more.onclick = () => {
      $('cleanup-rules').open = true;
      $('cleanup-search').value = tag;
      renderCleanupTags();
      $('cleanup-tags').querySelector('select')?.focus();
    };
    if (Object.hasOwn(preview.mappings, key)) {
      // Previewed from an unsaved edit: it counts only once the mappings are saved.
      const pending = el('span', 'Unsaved · save tag mappings to keep', 'cleanup-tag-badge unsaved');
      row.append(name, pending, more);
    } else {
      row.append(name, recognize, more);
    }
    return row;
  }));
}

function updateCleanupApply() {
  const session = cleanupSession;
  let changes = 0;
  for (const choice of session.approved.values()) changes += (choice.title ? 1 : 0) + choice.tags.size;
  const books = session.approved.size;
  const button = $('cleanup-apply');
  button.disabled = session.pending || session.stale || !books;
  button.textContent = books
    ? `Apply ${changes} ${changes === 1 ? 'change' : 'changes'} to ${books} ${books === 1 ? 'book' : 'books'}`
    : 'Apply selected';
  const toggle = $('cleanup-select-all');
  const total = (session.preview?.books || []).reduce((sum, book) => sum + book.changes.length + (book.proposedTitle ? 1 : 0), 0);
  toggle.hidden = !total;
  toggle.textContent = changes === total ? 'Clear all' : 'Select all';
}

// Mirrors genre_cleanup.choose_changes; the service recomputes the result when applying.
function chosenGenres(book, accepted) {
  const replacements = new Map(book.changes.map(change => [change.index, change.replacement]));
  // Upper- then lower-casing approximates Python's casefold (ß → ss, ς → σ).
  const key = tag => tag.split(/\s+/).filter(Boolean).join(' ').toUpperCase().toLowerCase().normalize('NFC');
  const result = [];
  const seen = new Set();
  book.current.forEach((tag, index) => {
    const value = accepted.has(index) ? replacements.get(index) : tag;
    if (value && !seen.has(key(value))) {
      result.push(value);
      seen.add(key(value));
    }
  });
  return result;
}

function cleanupChoice(book, kind, value, before, after, reason, refresh) {
  const session = cleanupSession;
  const label = el('label', null, 'cleanup-change');
  const input = el('input');
  input.type = 'checkbox';
  input.dataset.cleanup = 'change';
  input.onchange = () => {
    const choice = session.approved.get(book.path) || {title: false, tags: new Set()};
    if (kind === 'title') choice.title = input.checked;
    else if (input.checked) choice.tags.add(value);
    else choice.tags.delete(value);
    if (choice.title || choice.tags.size) session.approved.set(book.path, choice);
    else session.approved.delete(book.path);
    refresh();
    updateCleanupApply();
  };
  const text = el('span', null, 'cleanup-change-text');
  text.append(el('span', before, 'cleanup-before'), el('span', '→', 'cleanup-arrow'),
    el('span', after || 'remove', after ? 'cleanup-after' : 'cleanup-after cleanup-removed'));
  if (reason === 'Uses your saved tag mapping.') text.append(el('span', 'Your mapping', 'cleanup-tag-badge'));
  else if (reason) text.append(el('small', reason));
  label.append(input, text);
  return label;
}

function renderCleanupPreview() {
  const {preview} = cleanupSession;
  const changed = preview.books.length;
  const summary = [`${preview.scanned} scanned`, `${changed} ${changed === 1 ? 'book' : 'books'} with suggested changes`];
  if (preview.skipped) summary.push(`${preview.skipped} unreadable`);
  $('cleanup-status').textContent = summary.join(' · ');
  $('cleanup-rules').hidden = false;
  const savedCount = Object.keys(preview.savedMappings).length;
  $('cleanup-rules-heading').textContent = `Tag mappings · ${preview.tags.length} tags` + (savedCount ? ` · ${savedCount} saved` : '');
  renderUnrecognizedTags();
  $('cleanup-genres').replaceChildren(...preview.genres.map(genre => {
    const option = el('option');
    option.value = genre;
    return option;
  }));
  renderCleanupTags();
  const books = $('cleanup-books');
  books.replaceChildren();
  cleanupSession.approved.clear();
  const shownBooks = new Map(library.books.map(book => [book.path, book]));
  for (const book of preview.books) {
    const card = el('article', null, 'duplicate-card cleanup-book');
    const body = el('div');
    const shown = shownBooks.get(book.path);
    if (shown?.hasCover) card.append(cover(shown));
    body.append(el('strong', book.title), el('small', `${book.author} · ${book.path}`, 'cleanup-path'));
    const after = el('div');
    const result = el('div');
    const refresh = () => {
      const choice = cleanupSession.approved.get(book.path);
      const genres = chosenGenres(book, choice?.tags || new Set());
      result.textContent = genres.join(' · ') || 'Untagged';
      after.classList.toggle('cleanup-unchanged', !choice);
    };
    const changes = el('div', null, 'cleanup-changes');
    if (book.proposedTitle) {
      changes.append(cleanupChoice(book, 'title', null, book.title, book.proposedTitle,
        'Title · the suffix repeats the embedded series and book number.', refresh));
    }
    for (const change of book.changes) {
      changes.append(cleanupChoice(book, 'tag', change.index, change.tag, change.replacement, change.reason, refresh));
    }
    body.append(changes);
    if (book.changes.length) {
      const comparison = el('div', null, 'cleanup-comparison');
      const before = el('div');
      before.append(el('small', 'Genres now'), el('div', book.current.join(' · ') || 'Untagged'));
      after.append(el('small', 'After your choices'), result);
      comparison.append(before, after);
      body.append(comparison);
      refresh();
    }
    if (book.unmapped.length) body.append(el('small', `Not recognized, left unchanged: ${book.unmapped.join(', ')}`));
    card.append(body);
    books.append(card);
  }
  if (!books.firstChild) books.append(el('p', 'No suggested changes.'));
  updateCleanupApply();
}

async function scanGenres(saveMappings = false) {
  const session = cleanupSession;
  if (session.pending) return;
  if ([...session.modes].some(([key, mode]) => mode === 'map' && !session.draft[key])) {
    $('cleanup-rules-status').textContent = 'Enter a genre for every mapping before continuing.';
    $('cleanup-scope').value = session.scope;
    return;
  }
  const scope = $('cleanup-scope').value;
  session.pending = true;
  session.stale = true;
  updateCleanupApply();
  $('cleanup-status').textContent = 'Reading embedded tags…';
  $('cleanup-rules').inert = true;
  $('cleanup-unrecognized').inert = true;
  $('cleanup-scope').disabled = true;
  $('cleanup-close').disabled = saveMappings;
  $('cleanup-books').replaceChildren();
  try {
    const request = {scope};
    if (scope === 'folder') request.folder = session.folder;
    if (scope === 'selection') request.paths = session.paths;
    if (session.dirty || saveMappings) request.mappings = session.draft;
    if (saveMappings) request.saveMappings = true;
    const before = session.preview?.savedMappings || {};
    const preview = await mutation('/api/genre-cleanup-preview', request, {updateStorage: false, root: session.root});
    if (cleanupSession !== session || !cleanupDialog.open) return;
    if (saveMappings) session.savedNotice = savedMappingNotice(before, preview);
    if (session.root !== library.root) throw Error('The library changed. Reopen genre cleanup to scan again.');
    session.preview = preview;
    session.stale = false;
    session.scope = scope;
    session.draft = Object.assign(Object.create(null), preview.mappings);
    session.modes.clear();
    if (saveMappings) session.dirty = false;
    $('cleanup-rules-status').textContent = saveMappings ? 'Mappings saved.' :
      session.dirty ? 'Preview updated. Save mappings to reuse them later.' : '';
    renderCleanupPreview();
    showSavedNotice(session.savedNotice);
    session.savedNotice = null;
    session.recognized = null;
  } catch (error) {
    if (cleanupSession !== session || !cleanupDialog.open) return;
    session.recognized = null;
    session.savedNotice = null;
    if (session.preview) renderCleanupPreview();
    $('cleanup-status').textContent = error.message;
    $('cleanup-scope').value = session.scope;
  } finally {
    session.pending = false;
    if (cleanupSession === session) {
      $('cleanup-rules').inert = false;
      $('cleanup-unrecognized').inert = false;
      $('cleanup-scope').disabled = false;
      $('cleanup-close').disabled = false;
      updateCleanupApply();
    }
  }
}

$('open-genre-cleanup').onclick = () => {
  if (busy || loading || !library.connected || library.root !== library.macRoot) return;
  $('shelf-actions').open = false;
  const scope = $('cleanup-scope');
  scope.replaceChildren();
  const options = [['library', 'All Mac books']];
  if (folder !== null) options.push(['folder', folder === '' ? 'Books in root' : `Folder · ${folder}`]);
  if (selectedBooks.size) options.push(['selection', `Selected books · ${selectedBooks.size}`]);
  for (const [value, label] of options) {
    const option = el('option', label);
    option.value = value;
    scope.append(option);
  }
  scope.value = options.at(-1)[0];
  cleanupSession = {
    root: library.root, folder, paths: [...selectedBooks], scope: scope.value,
    approved: new Map(), stale: true, draft: Object.create(null), modes: new Map(), dirty: false, pending: false,
  };
  $('cleanup-rules').hidden = true;
  $('cleanup-rules').open = false;
  $('cleanup-unrecognized').hidden = true;
  $('cleanup-unrecognized').open = true;
  $('cleanup-unrecognized-status').textContent = '';
  $('cleanup-saved').hidden = true;
  $('cleanup-search').value = '';
  $('cleanup-apply-status').textContent = '';
  $('cleanup-errors').hidden = true;
  cleanupDialog.showModal();
  scanGenres();
};
$('cleanup-select-all').onclick = () => {
  const boxes = [...$('cleanup-books').querySelectorAll('input[data-cleanup="change"]')];
  const select = boxes.some(box => !box.checked);
  for (const box of boxes) {
    if (box.checked !== select) {
      box.checked = select;
      box.dispatchEvent(new Event('change'));
    }
  }
};
$('cleanup-scope').onchange = () => scanGenres();
$('cleanup-search').oninput = renderCleanupTags;
$('cleanup-preview').onclick = () => scanGenres();
$('cleanup-save').onclick = () => scanGenres(true);
cleanupDialog.addEventListener('cancel', event => {
  if ($('cleanup-close').disabled) event.preventDefault();
});


$('cleanup-apply').onclick = async () => {
  const session = cleanupSession;
  if (session.pending || session.stale || !session.approved.size) return;
  const jobs = session.preview.books.filter(book => session.approved.has(book.path));
  const errors = $('cleanup-errors');
  const status = $('cleanup-apply-status');
  errors.replaceChildren();
  errors.hidden = true;
  session.pending = true;
  updateCleanupApply();
  $('cleanup-books').inert = true;
  $('cleanup-rules').inert = true;
  $('cleanup-unrecognized').inert = true;
  $('cleanup-scope').disabled = true;
  $('cleanup-close').disabled = true;
  setBusy(true);
  let saved = 0;
  let failed = 0;
  try {
    for (const [index, book] of jobs.entries()) {
      status.textContent = `Saving ${index + 1} of ${jobs.length}…`;
      const choice = session.approved.get(book.path);
      const request = {path: book.path, revision: book.revision, mappings: session.preview.mappings};
      if (choice.title) request.title = book.proposedTitle;
      if (choice.tags.size) request.acceptedChanges = [...choice.tags];
      try {
        const data = await mutation('/api/metadata-cleanup-apply', request, {updateStorage: false, root: session.root});
        const shown = library.books.find(entry => entry.path === book.path);
        if (shown) updateBookMetadata(shown, data);
        saved++;
        if (data.warning) errors.append(el('li', `${book.title} · ${data.warning}`));
      } catch (error) {
        failed++;
        errors.append(el('li', `${book.title} · ${error.message}`));
      }
    }
    try {
      await readLibrary();
    } catch (error) {
      errors.append(el('li', `Could not refresh the shelf · ${error.message}`));
    }
    status.textContent = `${saved} saved · ${failed} failed`;
    errors.hidden = !errors.childElementCount;
  } finally {
    session.pending = false;
    $('cleanup-books').inert = false;
    setBusy(false);
    render();
    await scanGenres();
  }
};

const appleBooksDialog = $('apple-books-dialog');
let appleBooksSession = null;

function appleBooksRow(row, ambiguous) {
  const label = el('label', null, 'cleanup-change');
  const input = el('input');
  input.type = 'checkbox';
  input.value = row.path;
  input.onchange = updateAppleBooksAdd;
  const text = el('span', null, 'cleanup-change-text');
  text.append(el('span', row.title, 'cleanup-after'), el('span', row.author, 'cleanup-before'));
  if (ambiguous) {
    for (const match of row.books) {
      text.append(el('small', `Apple Books has: ${match.title} · ${match.author || 'Unknown author'}${match.store ? ' (Book Store)' : ''}`));
    }
  }
  text.append(el('small', row.path));
  label.append(input, text);
  return label;
}

function appleBooksChoices() {
  return [...appleBooksDialog.querySelectorAll('#apple-books-missing-list input:checked, #apple-books-ambiguous-list input:checked')]
    .map(input => input.value);
}

function updateAppleBooksAdd() {
  const count = appleBooksChoices().length;
  const button = $('apple-books-add');
  button.disabled = !count || !appleBooksSession || appleBooksSession.pending;
  button.textContent = count ? `Add ${count} ${count === 1 ? 'book' : 'books'} to Apple Books` : 'Add to Apple Books';
  const boxes = [...$('apple-books-missing-list').querySelectorAll('input')];
  $('apple-books-select-all').textContent = boxes.length && boxes.every(box => box.checked) ? 'Clear all' : 'Select all';
}

async function reviewAppleBooks(note = '') {
  const session = appleBooksSession;
  session.pending = true;
  updateAppleBooksAdd();
  $('apple-books-status').textContent = 'Reading Apple Books’ library…';
  for (const id of ['apple-books-access', 'apple-books-missing', 'apple-books-ambiguous']) $(id).hidden = true;
  $('apple-books-missing-list').replaceChildren();
  $('apple-books-ambiguous-list').replaceChildren();
  try {
    const preview = await mutation('/api/apple-books-preview', {}, {updateStorage: false, root: session.root});
    if (appleBooksSession !== session || !appleBooksDialog.open) return;
    session.preview = preview;
    if (preview.state === 'needs-access') {
      $('apple-books-access').hidden = false;
      $('apple-books-status').textContent = '';
      return;
    }
    if (preview.state !== 'ok') {
      $('apple-books-status').textContent = 'Apple Books isn’t available on this computer.';
      return;
    }
    const missing = preview.missing.length;
    $('apple-books-status').textContent = [note, `${preview.present} already in Apple Books`,
      preview.waiting && `${preview.waiting} added and waiting for Apple Books to list them`,
      `${missing} not in Apple Books`].filter(Boolean).join(' · ');
    $('apple-books-missing').hidden = !missing;
    $('apple-books-missing-heading').textContent = `Not in Apple Books (${missing})`;
    $('apple-books-missing-list').replaceChildren(...preview.missing.map(row => appleBooksRow(row, false)));
    $('apple-books-ambiguous').hidden = !preview.ambiguous.length;
    $('apple-books-ambiguous-heading').textContent = `Same title, different author (${preview.ambiguous.length})`;
    $('apple-books-ambiguous-list').replaceChildren(...preview.ambiguous.map(row => appleBooksRow(row, true)));
  } catch (error) {
    if (appleBooksSession === session) $('apple-books-status').textContent = error.message;
  } finally {
    if (appleBooksSession === session) {
      session.pending = false;
      updateAppleBooksAdd();
    }
  }
}

$('open-apple-books').onclick = () => {
  if (busy || loading || !library.connected || library.root !== library.macRoot) return;
  $('shelf-actions').open = false;
  appleBooksSession = {root: library.root, pending: false, preview: null};
  $('apple-books-errors').hidden = true;
  $('apple-books-progress').checked = appSettings.appleBooksProgress;
  appleBooksDialog.showModal();
  reviewAppleBooks();
};
$('apple-books-retry').onclick = () => reviewAppleBooks();
$('apple-books-progress').onchange = async () => {
  const input = $('apple-books-progress');
  input.disabled = true;
  try {
    await saveSetting('appleBooksProgress', input.checked);
  } catch (error) {
    input.checked = !input.checked;
    $('apple-books-status').textContent = error.message;
  } finally {
    input.disabled = false;
  }
};
$('apple-books-settings').onclick = () => mutation('/api/full-disk-access', {}, {updateStorage: false}).catch(error => {
  $('apple-books-status').textContent = error.message;
});
$('apple-books-select-all').onclick = () => {
  const boxes = [...$('apple-books-missing-list').querySelectorAll('input')];
  const select = boxes.some(box => !box.checked);
  for (const box of boxes) box.checked = select;
  updateAppleBooksAdd();
};
$('apple-books-add').onclick = async () => {
  const session = appleBooksSession;
  const paths = appleBooksChoices();
  if (!paths.length || session.pending) return;
  session.pending = true;
  updateAppleBooksAdd();
  setBusy(true);
  $('apple-books-close').disabled = true;
  $('apple-books-status').textContent = `Adding ${paths.length} ${paths.length === 1 ? 'book' : 'books'}…`;
  const errors = $('apple-books-errors');
  try {
    const result = await mutation('/api/apple-books-add', {paths, review: session.preview.review}, {updateStorage: false, root: session.root});
    errors.replaceChildren(...result.errors.map(error => el('li', error)));
    errors.hidden = !result.errors.length;
    $('apple-books-status').textContent = `Added ${result.added} · Apple Books usually lists new books within a minute…`;
    // Give Books time to import, then check what it lists now.
    await new Promise(resolve => setTimeout(resolve, 8000));
    if (appleBooksSession === session && appleBooksDialog.open) {
      session.pending = false;
      await reviewAppleBooks(`Added ${result.added}${result.failed ? ` · ${result.failed} failed` : ''}`);
    }
  } catch (error) {
    $('apple-books-status').textContent = error.message;
  } finally {
    session.pending = false;
    $('apple-books-close').disabled = false;
    setBusy(false);
    updateAppleBooksAdd();
  }
};
appleBooksDialog.addEventListener('cancel', event => { if ($('apple-books-close').disabled) event.preventDefault(); });

const splitDialog = $('split-dialog');
let splitSession;

function splitPart(start, title) {
  const row = el('div', null, 'duplicate-card split-part');
  const titleInput = el('input', null, 'field');
  titleInput.value = title;
  titleInput.maxLength = 500;
  const startInput = el('select', null, 'field');
  for (const section of splitSession.sections) {
    const option = el('option', `${section.start + 1} · ${section.label}`);
    option.value = section.start;
    startInput.append(option);
  }
  startInput.value = start;
  const skip = el('input');
  skip.type = 'checkbox';
  const remove = el('button', 'Remove volume', 'button');
  remove.type = 'button';
  remove.onclick = () => {
    row.remove();
    updateSplitControls();
  };
  for (const [label, input] of [['Title', titleInput], ['Start page', startInput], ['Skip export for this volume', skip]]) {
    const field = el('label', label);
    field.append(input);
    row.append(field);
  }
  const range = el('small', '', 'split-range');
  startInput.onchange = updateSplitControls;
  row.append(range, remove);
  $('split-parts').append(row);
  updateSplitControls();
}

function updateSplitControls() {
  const rows = Array.from($('split-parts').children).sort((a, b) =>
    Number(a.querySelector('select').value) - Number(b.querySelector('select').value));
  const starts = rows.map(row => Number(row.querySelector('select').value));
  const count = rows.length;
  rows.forEach((row, index) => {
    const end = starts[index + 1] ?? splitSession?.sections.length;
    row.querySelector('.split-range').textContent = end > starts[index] ?
      `Reading sections ${starts[index] + 1}–${end}` : 'Choose a unique start.';
  });
  $('split-add').disabled = !splitSession?.revision || count >= Math.min(16, splitSession.sections.length);
  $('split-export').disabled = !splitSession?.revision || count < 2 || new Set(starts).size !== count;
}

async function openSplit(book) {
  $('detail').close();
  splitSession = null;
  $('split-series').value = book.series || '';
  $('split-parts').replaceChildren();
  updateSplitControls();
  $('split-status').textContent = 'Reading the table of contents…';
  $('split-close').disabled = true;
  splitDialog.showModal();
  try {
    const preview = await mutation('/api/epub-split', {path: book.path}, {updateStorage: false});
    splitSession = {...preview, path: book.path, root: library.root};
    if (!$('split-series').value && /hyperion cantos/i.test(book.title) &&
        preview.suggested.map(part => part.title).join('|') === 'Hyperion|The Fall of Hyperion|Endymion|The Rise of Endymion') {
      $('split-series').value = 'Hyperion Cantos';
    }
    for (const part of preview.suggested.slice(0, 16)) splitPart(part.start, part.title);
    const existing = preview.suggested.filter(part => library.books.some(entry =>
      entry.path !== book.path && entry.title.toLocaleLowerCase() === part.title.toLocaleLowerCase()));
    $('split-status').textContent = existing.length ? `Already on this shelf: ${existing.map(part => part.title).join(', ')}. You can skip those exports.` :
      preview.suggested.length >= 2 ? 'Review the suggested starting pages.' : 'Add volumes and choose their starting pages.';
    updateSplitControls();
  } catch (error) {
    $('split-status').textContent = error.message;
  } finally {
    $('split-close').disabled = false;
  }
}

$('split-add').onclick = () => {
  const used = new Set(Array.from($('split-parts').querySelectorAll('select'), select => Number(select.value)));
  const next = splitSession.sections.find(section => !used.has(section.start));
  if (next) splitPart(next.start, next.label);
};
splitDialog.addEventListener('cancel', event => {
  if ($('split-close').disabled) event.preventDefault();
});
$('split-export').onclick = async () => {
  const parts = Array.from($('split-parts').children, row => ({
    start: Number(row.querySelector('select').value),
    title: row.querySelector('input').value.trim(),
    skip: row.querySelector('input[type=checkbox]').checked,
  })).sort((a, b) => a.start - b.start);
  $('split-close').disabled = true;
  $('split-add').disabled = true;
  $('split-export').disabled = true;
  $('split-parts').inert = true;
  $('split-series').disabled = true;
  $('split-status').textContent = 'Exporting EPUBs…';
  setBusy(true);
  try {
    const result = await mutation('/api/epub-split', {
      path: splitSession.path, revision: splitSession.revision, parts, series: $('split-series').value,
    }, {root: splitSession.root, updateStorage: false});
    $('split-status').textContent = `Exported ${result.files.length} EPUBs to ${result.folder}.`;
    splitSession = null;
    $('split-parts').replaceChildren();
    try {
      await readLibrary();
      folder = result.folder;
      render();
    } catch {
      $('split-status').textContent += ' Reopen the library to refresh the shelf.';
    }
  } catch (error) {
    $('split-status').textContent = error.message;
  } finally {
    setBusy(false);
    $('split-close').disabled = false;
    $('split-parts').inert = false;
    $('split-series').disabled = false;
    updateSplitControls();
  }
};

function readingStatusEditor(book) {
  const panel = el('section', null, 'reading-status-editor');
  const heading = el('h3', 'KOReader status');
  const options = el('div', null, 'reading-status-options');
  options.setAttribute('role', 'group');
  options.setAttribute('aria-label', 'KOReader status');
  const progress = el('div');
  const note = el('small', book.readingStatusPending ? 'Pending Kindle sync' : 'Select a status to save it.');
  note.setAttribute('role', 'status');
  const buttons = new Map();
  const update = () => {
    for (const [status, button] of buttons) {
      button.setAttribute('aria-pressed', String(status === book.readingStatus));
    }
    progress.replaceChildren(readingProgress(book, false));
  };
  for (const [status, title] of Object.entries(readingLabels)) {
    const button = el('button', title);
    button.type = 'button';
    buttons.set(status, button);
    options.append(button);
    button.onclick = async () => {
      if (busy || status === book.readingStatus) return;
      options.setAttribute('aria-busy', 'true');
      for (const choice of buttons.values()) choice.setAttribute('aria-disabled', 'true');
      note.textContent = 'Saving…';
      setBusy(true);
      try {
        const result = await mutation('/api/reading-status', {path: book.path, status}, {updateStorage: false});
        Object.assign(book, result);
        update();
        note.textContent = result.readingStatusPending ? 'Pending Kindle sync' : 'Saved to Kindle';
        render();
        if (device.kindle && appSettings.kindleAutoCompare) void compareLibraries(device.kindle, true);
      } catch (error) {
        note.textContent = error.message;
      } finally {
        options.removeAttribute('aria-busy');
        for (const choice of buttons.values()) choice.removeAttribute('aria-disabled');
        setBusy(false);
      }
    };
  }
  panel.append(heading, options, progress, note);
  update();
  return panel;
}

function details(b) {
  const dl=el('dl');
  for(const [k,v] of [['Filename',b.filename],['Folder',b.folder||'Library root'],['Size',(b.size/1024/1024).toFixed(2)+' MB'],['Language',b.language||'Not specified'],['Series',seriesLabel(b)||'Not specified'],['Genres',b.genres?.join(', ')||'Untagged'],['Modified',new Date(b.modified*1000).toLocaleString()]]){
    const value = el('dd');
    value.append(v);
    dl.append(el('dt',k),value);
  }
  $('detail-content').replaceChildren(cover(b),el('h2',b.title),el('p',b.author,'detail-author'));
  if (library.root === library.macRoot || library.root === device.kindle) {
    $('detail-content').append(readingStatusEditor(b));
  } else if (b.readingStatus) {
    $('detail-content').append(readingProgress(b));
    if (b.booksProgress != null) $('detail-content').append(el('p', `Apple Books · ${percentLabel(b.booksProgress)}`, 'books-progress'));
  }
  $('detail-content').append(dl);
  if(b.description)$('detail-content').append(el('p',b.description,'description'));
  else if(b.hasDescription){
    const description=el('p',null,'description');
    $('detail-content').append(description);
    const query=new URLSearchParams({path:b.path,root:library.root});
    shelfRequest('/api/description?'+query).then(r=>r.ok?r.json():null).then(data=>{
      if(data?.description&&description.isConnected)description.textContent=data.description;else description.remove();
    }).catch(()=>description.remove());
  }
  if(b.warning)$('detail-content').append(el('p',b.warning));
  const move=el('button','Move to folder…','button primary');move.onclick=()=>{$('detail').close();openMove(b);};$('detail-content').append(move);
  const edit=el('button','Edit metadata…','button');edit.onclick=()=>{$('detail').close();metadataSelection=b;resetCover();$('metadata-title').value=b.title;$('metadata-authors').value=(b.authors?.length?b.authors:[b.author]).join('\n');$('metadata-genres').value=(b.genres||[]).join('\n');$('metadata-series').value=b.series||'';$('metadata-position').value=b.seriesPosition||'';$('metadata-position').disabled=!b.series;$('metadata-error').textContent='';$('metadata-dialog').showModal();};$('detail-content').append(edit);
  if (library.root === library.macRoot) {
    const split = el('button', 'Split omnibus…', 'button');
    split.onclick = () => openSplit(b);
    $('detail-content').append(split);
    if (usbWorkflow() === 'send') {
      const send = el('button', 'Send to Kindle…', 'button');
      send.onclick = () => {
        $('detail').close();
        openKindleDelivery([b]);
      };
      $('detail-content').append(send);
    }
  }
  $('detail').showModal();
}
async function readLibrary(ifChanged=false) {
  // With ifChanged, the service answers with only a signature when the listing is the same.
  const known=ifChanged&&library.connected&&library.signature;
  const response=await shelfRequest(known?'/api/library?known='+encodeURIComponent(known):'/api/library');
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'Could not read the library.');
  if(data.unchanged)return false; // Also covers a library switched while this check ran.
  library=data;
  if(folder&&!library.folders.includes(folder))folder=null;
  return true;
}
async function refresh(ifChanged=false) {
  if (sessionExpired) { showSessionPrompt(); return; }
  if(loading||busy)return; loading=true;
  if(!ifChanged){$('notice').textContent='Reading your library…';$('waiting-status').textContent='Finding your books…';}
  try { if(await readLibrary(ifChanged))render();$('notice').textContent=library.connected?'':library.root?'Library unavailable. Reconnect the Kindle or choose another folder.':''; }
  catch(e){if(!sessionExpired){$('notice').textContent=e.message+' Check that the local application is running.';$('waiting-status').textContent='Connection lost · restart koshelf in your terminal';}}
  finally{loading=false;renderConnection();$('remove-folder').disabled=busy||!folder; $('new-folder').disabled=!library.connected||busy;if(library.connected)checkConnection().catch(()=>{});}
}
function choose() {
  $('path').value=library.root||localStorage.getItem('kindle-shelf-root')||'';$('choose-error').textContent='';$('candidates').replaceChildren();
  for(const path of library.candidates){const b=el('button',path,'button');b.type='button';b.onclick=()=>{$('path').value=path;};$('candidates').append(b);}
  $('chooser').showModal();$('path').focus();
}
$('choose-form').onsubmit=async e=>{
  e.preventDefault(); const submit=e.submitter;submit.disabled=true;$('choose-error').textContent='Reading books…';
  try{const r=await shelfRequest('/api/library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:$('path').value.trim()})});const data=await r.json();if(!r.ok)throw Error(data.error||'Could not open folder');library=data;ejected=false;folder=null;lastMove=null;$('undo').hidden=true;localStorage.setItem('kindle-shelf-root',library.root);$('chooser').close();$('notice').textContent='';render();}
  catch(e){$('choose-error').textContent=e.message;}
  finally{submit.disabled=false;}
};
let searchTimer=0;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(render,120);};$('genre-filter').onchange=render;$('series-filter').onchange=render;$('reading-filter').onchange=render;$('sort').onchange=render;$('change').onclick=choose;$('connect').onclick=choose;
$('all').onclick=()=>{folder=null;render();};$('cancel').onclick=()=>$('chooser').close();$('close-detail').onclick=()=>$('detail').close();
for(const id of ['chooser','detail','folder-dialog','move-dialog','remove-dialog','metadata-dialog','bulk-dialog','bulk-move-dialog'])$(id).addEventListener('click',e=>{if(e.target===$(id)&&!bulkSaving){const r=$(id).getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$(id).close();}});
const shelfActions = $('shelf-actions');
const shelfActionsToggle = shelfActions.querySelector('summary');
shelfActions.addEventListener('click', event => {
  if (event.target.closest('button')) shelfActions.open = false;
});
document.addEventListener('click', event => {
  if (!shelfActions.contains(event.target)) shelfActions.open = false;
});
shelfActions.addEventListener('keydown', event => {
  if (event.key === 'Escape' && shelfActions.open) {
    shelfActions.open = false;
    shelfActionsToggle.focus();
  }
});
window.addEventListener('focus', () => {
  comparedAt=0; // Files may have changed in Finder or on the Kindle while away.
  // Give the file picker time to deliver its selection before rescanning.
  setTimeout(() => {
    if (library.connected && !shelfActions.open && !document.querySelector('dialog[open]')) refresh(true);
  }, 300);
});

let idleTicks=0;
setInterval(async()=>{
  if(sessionExpired||loading||busy||polling)return;
  // Poll about every 2 seconds while KoShelf is in the background.
  if(!document.hasFocus()&&++idleTicks%3)return;
  polling=true;
  try {
    const state=await checkConnection();if(!state||busy||loading)return;
    if(state.connected && (!library.connected || state.root!==library.root)) {
      folder=null;lastMove=null;$('undo').hidden=true;$('search').value='';
      for(const id of ['chooser','detail','folder-dialog','move-dialog','remove-dialog','metadata-dialog','bulk-dialog','bulk-move-dialog'])$(id).close();
      await refresh();
    } else if(!state.connected && library.connected) {
      library.connected=false;library.books=[];library.folders=[];lastMove=null;$('undo').hidden=true;
      for(const id of ['chooser','detail','folder-dialog','move-dialog','remove-dialog','metadata-dialog','bulk-dialog','bulk-move-dialog'])$(id).close();render();
    }
  } catch {if(!sessionExpired)$('waiting-status').textContent='Connection lost · restart koshelf in your terminal';}
  finally {polling=false;}
},750);
$('waiting-choose').onclick=choose;
$('waiting-create').onclick=async()=>{
  const button=$('waiting-create');button.disabled=true;
  try{
    const r=await shelfRequest('/api/create-mac-library',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const data=await r.json();if(!r.ok)throw Error(data.error||'Could not create the library.');
    library=data;folder=null;render();
  }catch(e){$('waiting-message').textContent=e.message;}
  finally{button.disabled=false;}
};
async function loadSettings(){
  try{const r=await shelfRequest('/api/settings');if(r.ok)appSettings=await r.json();}catch{}
}
async function saveSetting(name,value){
  const r=await shelfRequest('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:{[name]:value}})});
  const data=await r.json();if(!r.ok)throw Error(data.error||'Could not save the setting.');
  appSettings=data;
  if(name==='appearance'||name==='theme')return;
  comparison=null;comparedAt=0;
  await refresh();
  await checkConnection().catch(()=>{});
}
const settingsDialog=$('settings-dialog');
$('open-settings').onclick=async()=>{
  $('shelf-actions').open=false;
  await loadSettings();
  for(const input of settingsDialog.querySelectorAll('input[data-setting]'))input.checked=!!appSettings[input.dataset.setting];
  const appearance=appSettings.appearance||document.documentElement.dataset.appearance||'system';
  for(const input of settingsDialog.querySelectorAll('input[name="appearance"]'))input.checked=input.value===appearance;
  const theme=appSettings.theme||document.documentElement.dataset.palette||'koshelf';
  for(const input of settingsDialog.querySelectorAll('input[name="theme"]'))input.checked=input.value===theme;
  $('settings-status').textContent='';
  settingsDialog.showModal();
};
for(const input of document.querySelectorAll('#settings-dialog input[data-setting]')){
  input.onchange=async()=>{
    input.disabled=true;
    try{await saveSetting(input.dataset.setting,input.checked);$('settings-status').textContent='Saved.';}
    catch(e){input.checked=!input.checked;$('settings-status').textContent=e.message;}
    finally{input.disabled=false;}
  };
}
// The light/dark switch in the window bar picks Light or Dark; Settings also offers System.
const themeToggle=$('theme-toggle');
function showThemeToggle(){
  const dark=document.documentElement.dataset.theme==='dark';
  themeToggle.setAttribute('aria-checked',String(dark));
  themeToggle.title=dark?'Switch to light mode':'Switch to dark mode';
}
new MutationObserver(showThemeToggle).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
showThemeToggle();
themeToggle.onclick=async()=>{
  const previous=document.documentElement.dataset.appearance||'system';
  const next=document.documentElement.dataset.theme==='dark'?'light':'dark';
  window.koshelfSetAppearance(next);
  try{await saveSetting('appearance',next);}
  catch(e){window.koshelfSetAppearance(previous);$('notice').textContent=e.message;}
};
// Appearance and theme change at once. If saving fails, the previous choice comes back.
for(const [name,attribute,apply,fallback] of [['appearance','appearance',window.koshelfSetAppearance,'system'],['theme','palette',window.koshelfSetTheme,'koshelf']]){
  for(const input of document.querySelectorAll(`#settings-dialog input[name="${name}"]`)){
    input.onchange=async()=>{
      const previous=document.documentElement.dataset[attribute]||fallback;
      apply(input.value);
      try{await saveSetting(name,input.value);$('settings-status').textContent='Saved.';}
      catch(e){
        apply(previous);
        for(const other of document.querySelectorAll(`#settings-dialog input[name="${name}"]`))other.checked=other.value===previous;
        $('settings-status').textContent=e.message;
      }
    };
  }
}
$('eject').onclick=async()=>{
  if(busy||loading)return;setBusy(true);$('eject').disabled=true;$('notice').textContent='Finishing up and asking macOS to eject your Kindle…';
  let message;
  try {
    await mutation('/api/eject', library.localFirst
      ? {kindle:device.kindle, usbDevice:device.usbKindle?.device}
      : {});
    ejected=true;device={};comparison=null;comparedAt=0;deviceRequest++;lastMove=null;$('undo').hidden=true;
    if(library.localFirst&&library.root!==library.macRoot&&library.canEject)await openLibrary(library.macRoot);
    else if(!library.localFirst)library={books:[],folders:[],candidates:[],connected:false,root:null};
    message='Kindle safely ejected. You can unplug it now.';
  }
  catch(e){$('notice').textContent=e.message;}
  finally{setBusy(false);render();if(message)$('notice').textContent=message;}
};

async function mutation(url, body, {updateStorage = true, root = library.root} = {}) {
  const r=await shelfRequest(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,library:root})});
  const data=await r.json();if(!r.ok)throw Error(data.error||'Operation failed');
  if(updateStorage&&!['/api/eject','/api/backup','/api/sync','/api/send-to-kindle'].includes(url)){try{const storage=await shelfRequest('/api/storage');if(storage.ok)library.storage=await storage.json();else library.storage=null;}catch{library.storage=null;}}
  return data;
}
function setBusy(value) {
  busy=value;$('backup').disabled=value||!library.connected;$('remove-folder').disabled=value;$('eject').disabled=value;$('change').disabled=value;$('undo').disabled=value;
  $('new-folder').disabled=value||!library.connected;
  document.body.classList.toggle('busy',value);renderConnection();
}
function folderOptions(select, selected='', excluded=null) {
  select.replaceChildren();for(const f of ['',...library.folders]) {if(f===excluded)continue;const option=el('option',f||'Books in root');option.value=f;option.selected=f===selected;select.append(option);}
}
const importDialog = $('import-dialog');
const importFolder = $('import-folder');
const importStatus = $('import-status');
const importList = $('import-preview');
const confirmImport = $('confirm-import');
let importFiles = [];
let readyImports = [];

function isFileDrop(event) {
  return event.dataTransfer?.types.includes('Files');
}

async function previewImports() {
  const root = library.root;
  const folder = importFolder.value;
  readyImports = [];
  importList.replaceChildren();
  confirmImport.disabled = importFolder.disabled = true;
  importStatus.textContent = 'Checking filenames and duplicates…';
  const digests = new Set();
  const names = new Set();
  setBusy(true);
  try {
    for (const file of importFiles) {
      try {
        if (!/\.epub$/i.test(file.name) || !file.size || file.size > 128 * 1024 * 1024) {
          throw Error('Choose an EPUB up to 128 MiB.');
        }
        const buffer = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        const digest = Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, '0')).join('');
        const name = file.name.normalize('NFC').toLowerCase();
        if (digests.has(digest)) throw Error('Duplicate in this import.');
        if (names.has(name)) throw Error('Another file in this import has the same name.');
        const result = await mutation('/api/import-preview', {folder, name: file.name, size: file.size, digest},
          {root, updateStorage: false});
        readyImports.push({file, digest});
        names.add(name);
        digests.add(digest);
        importList.append(el('li', result.path));
      } catch (error) {
        importList.append(el('li', `${file.name} · ${error.message}`));
      }
    }
    importStatus.textContent = `${readyImports.length} of ${importFiles.length} ready to copy.`;
  } finally {
    importFolder.disabled = false;
    confirmImport.disabled = !readyImports.length;
    setBusy(false);
  }
}

function openImport(files, target) {
  if (busy || loading || !library.connected || !files.length) return;
  importFiles = [...files];
  folderOptions(importFolder, target || '');
  importDialog.showModal();
  previewImports();
}

$('import-books').onclick = () => $('import-files').click();
$('import-files').onchange = event => {
  openImport(event.target.files, folder);
  event.target.value = '';
};
importFolder.onchange = previewImports;
importDialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
confirmImport.onclick = async () => {
  if (busy || !readyImports.length) return;
  const root = library.root;
  const destination = importFolder.value;
  let copied = 0;
  setBusy(true);
  confirmImport.disabled = importFolder.disabled = $('close-import').disabled = true;
  importList.replaceChildren();
  try {
    for (const {file, digest} of readyImports) {
      importStatus.textContent = `Copying ${file.name}…`;
      try {
        const query = new URLSearchParams({root, folder: destination, name: file.name, digest});
        const response = await shelfRequest('/api/import-epub?' + query, {
          method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
        });
        const result = await response.json();
        if (!response.ok) throw Error(result.error);
        copied++;
        importList.append(el('li', `Copied · ${result.path}`));
      } catch (error) {
        importList.append(el('li', `${file.name} · ${error.message}`));
      }
    }
    importStatus.textContent = `Copied ${copied} of ${readyImports.length} EPUBs.`;
    if (copied) await readLibrary();
  } catch (error) {
    importList.append(el('li', error.message));
  } finally {
    readyImports = [];
    $('close-import').disabled = false;
    setBusy(false);
    render();
  }
};

document.addEventListener('dragover', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = busy || loading || !library.connected ? 'none' : 'copy';
});
document.addEventListener('drop', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  if (!document.querySelector('dialog[open]')) openImport(event.dataTransfer.files, folder);
});

function dropTarget(node, target) {
  node.addEventListener('dragover',e=>{if(busy||(!isFileDrop(e)&&(!dragged||dragged.folder===target)))return;e.preventDefault();e.dataTransfer.dropEffect=isFileDrop(e)?'copy':'move';node.classList.add('drop-over');});
  node.addEventListener('dragleave',()=>node.classList.remove('drop-over'));
  node.addEventListener('drop',async e=>{e.preventDefault();node.classList.remove('drop-over');if(isFileDrop(e)){e.stopPropagation();openImport(e.dataTransfer.files,target);return;}document.body.classList.remove('drag-active');const book=dragged;dragged=null;if(!book||busy||book.folder===target)return;await performMove(book,target);});
}
async function performMove(book,target,undoing=false) {
  if(busy)return false;setBusy(true);$('notice').textContent=`Moving “${book.title}”…`;
  try {
    const data=await mutation('/api/move',{source:book.path,folder:target});
    const originalFolder=book.folder;book.path=data.path;book.folder=data.folder;
    if(undoing){lastMove=null;$('undo').hidden=true;}else{lastMove={book,folder:originalFolder,library:library.root};$('undo').hidden=false;}
    $('notice').textContent=data.warning || (undoing?'Move undone.':`Moved “${book.title}” to ${target||'Books in root'}.${data.withSidecar?' KOReader companion folder moved too.':''}`);
    return true;
  } catch(e) {$('notice').textContent=e.message;return false;}
  finally {setBusy(false);render();}
}
function openMove(book) {
  moveSelection=book;$('move-title').textContent=book.title;$('move-error').textContent='';
  folderOptions($('move-target'),'',book.folder);$('move-dialog').showModal();
}
$('new-folder').onclick=()=>{
  if(busy||!library.connected)return;folderOptions($('folder-parent'),folder||'');$('folder-name').value='';$('folder-error').textContent='';$('folder-dialog').showModal();$('folder-name').focus();
};
$('cancel-folder').onclick=()=>$('folder-dialog').close();$('cancel-move').onclick=()=>$('move-dialog').close();
$('folder-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;const submit=e.submitter;submit.disabled=true;setBusy(true);$('folder-error').textContent='';
  try {const data=await mutation('/api/folders',{parent:$('folder-parent').value,name:$('folder-name').value});library.folders.push(data.folder);library.folders.sort((a,b)=>a.localeCompare(b));folder=data.folder;$('search').value='';$('folder-dialog').close();$('notice').textContent=`Created “${data.folder}”. Drag a book onto it in the sidebar.`;}
  catch(err){$('folder-error').textContent=err.message;}
  finally{submit.disabled=false;setBusy(false);render();}
};
$('move-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;const target=$('move-target').value;
  if(target===moveSelection.folder||!$('move-target').options.length){$('move-error').textContent='Create another folder first.';return;}
  e.submitter.disabled=true;const ok=await performMove(moveSelection,target);e.submitter.disabled=false;
  if(ok)$('move-dialog').close();else $('move-error').textContent=$('notice').textContent;
};
$('undo').onclick=async()=>{if(lastMove&&lastMove.library===library.root)await performMove(lastMove.book,lastMove.folder,true);};
$('remove-folder').onclick=()=>{
  if(!folder||busy||loading)return;
  const parent=folder.includes('/')?folder.slice(0,folder.lastIndexOf('/')):'Books in root';
  $('remove-description').textContent=`“${folder}” will be removed. Its contents will move into “${parent}”.`;
  $('remove-error').textContent='';$('remove-dialog').showModal();
};
$('cancel-remove').onclick=()=>$('remove-dialog').close();
$('remove-form').onsubmit=async e=>{
  e.preventDefault();if(busy||!folder)return;
  const submit=e.submitter;submit.disabled=true;setBusy(true);$('remove-error').textContent='';
  try {
    const data=await mutation('/api/remove-folder',{folder});
    const prefix=data.removed+'/';const parentPrefix=data.parent?data.parent+'/':'';
    for(const b of library.books)if(b.path.startsWith(prefix)){
      b.path=parentPrefix+b.path.slice(prefix.length);
      b.folder=b.folder===data.removed?data.parent:parentPrefix+b.folder.slice(prefix.length);
    }
    library.folders=library.folders.filter(f=>f!==data.removed).map(f=>f.startsWith(prefix)?parentPrefix+f.slice(prefix.length):f).sort((a,b)=>a.localeCompare(b));
    folder=data.parent;lastMove=null;$('undo').hidden=true;$('search').value='';
    $('notice').textContent=data.warning||`Removed “${data.removed}”. All contents moved to ${data.parent||'Books in root'}.`;
    $('remove-dialog').close();
  } catch(err){$('remove-error').textContent=err.message;}
  finally{submit.disabled=false;setBusy(false);render();}
};
function resetCover() {
  coverSelection++;
  pendingCover = null;
  $('cover-file').value = '';
  $('cover-preview').replaceChildren(cover(metadataSelection));
  $('discard-cover').hidden = true;
  $('cover-status').hidden = true;
}

async function pickCover(file) {
  if (!file || busy) return;
  const selection = ++coverSelection;
  const status = $('cover-status');
  const save = $('metadata-form').querySelector('button[type="submit"]');
  status.hidden = false;
  status.textContent = 'Checking image…';
  save.disabled = true;
  try {
    if (file.size > 12 * 1024 * 1024) throw Error('Choose an image up to 12 MB.');
    const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    const jpeg = [255, 216, 255].every((byte, index) => signature[index] === byte);
    const png = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, index) => signature[index] === byte);
    if (!jpeg && !png) throw Error('Choose a JPEG or PNG image.');
    const src = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(Error('Could not read the image.'));
      reader.readAsDataURL(new Blob([file], {type: jpeg ? 'image/jpeg' : 'image/png'}));
    });
    const preview = el('img');
    preview.alt = 'New cover preview';
    preview.src = src;
    await preview.decode();
    if (selection !== coverSelection) return;
    pendingCover = src.split(',', 2)[1];
    $('cover-preview').replaceChildren(preview);
    $('discard-cover').hidden = false;
    status.textContent = file.name;
  } catch (error) {
    if (selection === coverSelection) status.textContent = error.message || 'Could not open the image.';
  } finally {
    if (selection === coverSelection) save.disabled = false;
  }
}

$('choose-cover').onclick = () => $('cover-file').click();
$('discard-cover').onclick = () => {
  resetCover();
  $('metadata-form').querySelector('button[type="submit"]').disabled = false;
};
$('cover-file').onchange = event => pickCover(event.target.files[0]);
$('cover-drop').addEventListener('dragover', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
  $('cover-drop').classList.toggle('drop-over', !busy);
});
$('cover-drop').addEventListener('dragleave', event => {
  if (!$('cover-drop').contains(event.relatedTarget)) $('cover-drop').classList.remove('drop-over');
});
$('cover-drop').addEventListener('drop', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  event.stopPropagation();
  $('cover-drop').classList.remove('drop-over');
  pickCover(event.dataTransfer.files[0]);
});
$('metadata-dialog').addEventListener('close', () => {
  coverSelection++;
  pendingCover = null;
  $('cover-preview').replaceChildren();
  $('metadata-form').querySelector('button[type="submit"]').disabled = false;
});

$('metadata-series').oninput = () => {
  $('metadata-position').disabled = !$('metadata-series').value.trim();
};
$('cancel-metadata').onclick=()=>$('metadata-dialog').close();
$('metadata-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;const submit=e.submitter;submit.disabled=true;setBusy(true);$('metadata-error').textContent='Saving and verifying your EPUB…';
  try {
    const book=metadataSelection;
    const title = $('metadata-title').value.trim();
    const authors = metadataLines($('metadata-authors').value);
    const genres = metadataLines($('metadata-genres').value);
    const series = $('metadata-series').value.trim();
    const seriesPosition = series ? $('metadata-position').value.trim() : '';
    const fields = {};
    if (title !== book.title) fields.title = title;
    if (JSON.stringify(authors) !== JSON.stringify(book.authors?.length ? book.authors : [book.author])) fields.authors = authors;
    if (JSON.stringify(genres) !== JSON.stringify(book.genres || [])) fields.genres = genres;
    if (series !== (book.series || '')) fields.series = series;
    if (series && seriesPosition !== (book.seriesPosition || '')) fields.seriesPosition = seriesPosition;
    if (pendingCover) fields.cover = pendingCover;
    if (!Object.keys(fields).length) {
      $('metadata-dialog').close();
      return;
    }
    const data = await mutation('/api/metadata', {...fields, path:book.path, size:book.size, modified:book.modified});
    updateBookMetadata(book, data);
    book.hasCover = data.hasCover;
    $('metadata-dialog').close();render();$('notice').textContent=data.warning||'Metadata saved. Original backed up on your Mac. Refresh cached book information in KOReader if the old names still appear.';
    details(book);
  }catch(err){$('metadata-error').textContent=err.message;}
  finally{submit.disabled=false;setBusy(false);}
};
function renderSelection() {
  const unavailable = busy || loading || !library.connected;
  $('select-books').disabled = unavailable || (!selectionMode && !library.books.length);
  $('select-books').textContent = selectionMode ? 'Done selecting' : 'Select books';
  $('select-books').setAttribute('aria-pressed', String(selectionMode));
  $('selection-bar').hidden = !selectionMode;
  $('selection-count').textContent = `${selectedBooks.size} selected`;
  $('select-visible').disabled = unavailable || !visibleBooks.some(book => !selectedBooks.has(book.path));
  $('clear-selection').disabled = unavailable || !selectedBooks.size;
  $('edit-selected').disabled = unavailable || !selectedBooks.size;
  $('send-selected').hidden = library.root !== library.macRoot || usbWorkflow() !== 'send';
  $('send-selected').disabled = unavailable || !selectedBooks.size;
  $('move-selected').disabled = unavailable || !selectedBooks.size;
}
$('select-books').onclick = () => {
  selectionMode = !selectionMode;
  selectedBooks.clear();
  render();
};
$('select-visible').onclick = () => {
  for (const book of visibleBooks) selectedBooks.add(book.path);
  render();
};
$('clear-selection').onclick = () => { selectedBooks.clear(); render(); };

function updateBulkMoveDestination() {
  const creating = $('bulk-move-create').checked;
  $('bulk-move-target-label').textContent = creating ? 'Create inside' : 'Destination folder';
  $('bulk-move-new').hidden = !creating;
  $('bulk-move-name').required = creating;
  $('save-bulk-move').textContent = creating ? 'Create folder and move' : 'Move books';
}
$('bulk-move-create').onchange = updateBulkMoveDestination;
$('move-selected').onclick = () => {
  $('bulk-move-form').reset();
  folderOptions($('bulk-move-target'), folder || '');
  $('bulk-move-count').textContent = `${selectedBooks.size} selected`;
  $('bulk-move-status').textContent = '';
  $('bulk-move-progress').hidden = true;
  $('bulk-move-errors').replaceChildren();
  $('bulk-move-errors').hidden = true;
  $('close-bulk-move').textContent = 'Cancel';
  $('save-bulk-move').hidden = false;
  updateBulkMoveDestination();
  $('bulk-move-dialog').showModal();
};
$('close-bulk-move').onclick = () => $('bulk-move-dialog').close();
$('bulk-move-dialog').addEventListener('cancel', event => { if (bulkSaving) event.preventDefault(); });
$('bulk-move-form').onsubmit = async event => {
  event.preventDefault();
  if (busy || $('save-bulk-move').hidden) return;
  const root = library.root;
  const books = library.books.filter(book => selectedBooks.has(book.path));
  const requestOptions = {updateStorage: false, root};
  const controls = [...$('bulk-move-form').querySelectorAll('button, select, input')];
  const progress = $('bulk-move-progress');
  const status = $('bulk-move-status');
  const errors = $('bulk-move-errors');
  let target = $('bulk-move-target').value;
  let moved = 0;
  let unchanged = 0;
  let failed = 0;
  let completed = false;
  controls.forEach(control => { control.disabled = true; });
  bulkSaving = true;
  setBusy(true);
  status.textContent = 'Preparing destination…';
  try {
    if ($('bulk-move-create').checked) {
      const result = await mutation('/api/folders', {parent: target, name: $('bulk-move-name').value}, requestOptions);
      target = result.folder;
      library.folders.push(target);
      library.folders.sort((a, b) => a.localeCompare(b));
    }
    progress.max = books.length;
    progress.value = 0;
    progress.hidden = false;
    for (const book of books) {
      const source = book.path;
      status.textContent = `Processing ${progress.value + 1} of ${books.length} · ${book.title}`;
      if (book.folder === target) {
        unchanged++;
        selectedBooks.delete(source);
      } else {
        try {
          const result = await mutation('/api/move', {source, folder: target}, requestOptions);
          Object.assign(book, {path: result.path, folder: result.folder});
          selectedBooks.delete(source);
          moved++;
          if (result.warning) errors.append(el('li', `${source} · ${result.warning}`));
        } catch (error) {
          failed++;
          errors.append(el('li', `${source} · ${error.message}`));
        }
      }
      progress.value++;
    }
    if (moved) {
      lastMove = null;
      $('undo').hidden = true;
    }
    try { await readLibrary(); }
    catch (error) { errors.append(el('li', `Could not refresh the shelf · ${error.message}`)); }
    if (library.root === root && library.connected && !failed) {
      folder = target;
      $('search').value = '';
      $('genre-filter').value = '';
      $('series-filter').value = '';
      $('reading-filter').value = '';
      selectionMode = false;
    }
    const summary = `Moved ${moved} to ${target || 'Books in root'} · Already there ${unchanged} · Failed ${failed}`;
    status.textContent = $('notice').textContent = summary;
    errors.hidden = !errors.childElementCount;
    completed = true;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    bulkSaving = false;
    controls.forEach(control => { control.disabled = false; });
    if (completed) {
      $('close-bulk-move').textContent = 'Done';
      $('save-bulk-move').hidden = true;
    }
    setBusy(false);
    render();
  }
};

function updateBookMetadata(book, data) {
  const {title, author, authors, genres, series, seriesPosition, size, modified} = data;
  Object.assign(book, {title, author, authors, genres, series, seriesPosition, size, modified});
}

const bulkInputs = ['bulk-change-series', 'bulk-series', 'bulk-change-authors', 'bulk-genre-action', 'bulk-authors', 'bulk-genres'];
const metadataLines = value => value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);

function updateBulkInputs() {
  const changeAuthors = $('bulk-change-authors').checked;
  const action = $('bulk-genre-action').value;
  $('bulk-authors').disabled = !changeAuthors;
  $('bulk-authors').required = changeAuthors;
  $('bulk-genres').disabled = !action;
  $('bulk-genres').required = action === 'add' || action === 'remove';
  const changeSeries = $('bulk-change-series').checked;
  $('bulk-series').disabled = !changeSeries;
  $('bulk-series').required = changeSeries;
  $('save-bulk').disabled = !changeAuthors && !action && !changeSeries;
}
$('bulk-change-series').onchange = $('bulk-change-authors').onchange = $('bulk-genre-action').onchange = updateBulkInputs;
$('edit-selected').onclick = () => {
  $('bulk-form').reset();
  $('bulk-count').textContent = `Review ${selectedBooks.size} selected books`;
  $('bulk-books').replaceChildren(...library.books.filter(book => selectedBooks.has(book.path))
    .map(book => el('li', `${book.title} · ${book.path}`)));
  $('bulk-progress').hidden = true;
  $('bulk-status').textContent = '';
  $('bulk-errors').replaceChildren();
  $('bulk-errors').hidden = true;
  $('close-bulk').textContent = 'Cancel';
  $('save-bulk').hidden = false;
  $('bulk-change-series').disabled = $('bulk-change-authors').disabled = $('bulk-genre-action').disabled = false;
  updateBulkInputs();
  $('bulk-dialog').showModal();
};
$('bulk-dialog').addEventListener('cancel', event => { if (bulkSaving) event.preventDefault(); });
$('close-bulk').onclick = () => $('bulk-dialog').close();

$('bulk-form').onsubmit = async event => {
  event.preventDefault();
  if (busy || $('save-bulk').disabled || $('save-bulk').hidden) return;
  const authors = $('bulk-change-authors').checked ? metadataLines($('bulk-authors').value) : undefined;
  const action = $('bulk-genre-action').value;
  const series = $('bulk-change-series').checked ? $('bulk-series').value.trim() : undefined;
  if (series === '') {
    $('bulk-status').textContent = 'Enter a series name.';
    return;
  }
  const genreKey = genre => genre.toLocaleLowerCase();
  const requestedGenres = new Map(metadataLines($('bulk-genres').value)
    .map(genre => genre.replace(/\s+/g, ' ')).map(genre => [genreKey(genre), genre]));
  if ((authors && (!authors.length || authors.length > 50 || authors.some(author => author.length > 500)))
      || ((action === 'add' || action === 'remove') && !requestedGenres.size)) {
    $('bulk-status').textContent = 'Enter up to 50 authors (500 characters per name), or enter at least one genre to add or remove.';
    return;
  }

  const books = library.books.filter(book => selectedBooks.has(book.path));
  const root = library.root;
  const progress = $('bulk-progress');
  progress.hidden = false;
  progress.max = books.length;
  progress.value = 0;
  $('save-bulk').disabled = $('close-bulk').disabled = true;
  for (const id of bulkInputs) $(id).disabled = true;
  bulkSaving = true;
  setBusy(true);
  let saved = 0;
  let unchanged = 0;
  let failed = 0;
  const same = (left, right) => left.length === right.length && left.every((item, index) => item === right[index]);
  try {
    for (const book of books) {
      $('bulk-status').textContent = `Checking ${progress.value + 1} of ${books.length} · ${book.title}`;
      const existing = book.genres || [];
      const existingKeys = new Set(existing.map(genreKey));
      let genres = existing;
      if (action === 'replace') genres = [...requestedGenres.values()];
      if (action === 'add') genres = [...existing, ...[...requestedGenres].filter(([key]) => !existingKeys.has(key)).map(([, genre]) => genre)];
      if (action === 'remove') genres = existing.filter(genre => !requestedGenres.has(genreKey(genre)));
      const fields = {};
      if (authors && !same(authors, book.authors || [])) fields.authors = authors;
      if (!same(genres, existing)) fields.genres = genres;
      if (series !== undefined && series !== (book.series || '')) {
        fields.series = series;
        if (seriesKey(series) !== seriesKey(book.series)) fields.seriesPosition = '';
      }
      if (Object.keys(fields).length) {
        try {
          const data = await mutation('/api/metadata', {
            ...fields, path: book.path, size: book.size, modified: book.modified
          }, {updateStorage: false, root});
          updateBookMetadata(book, data);
          saved++;
          if (data.warning) $('bulk-errors').append(el('li', `${book.path} · ${data.warning}`));
        } catch (error) {
          failed++;
          $('bulk-errors').append(el('li', `${book.path} · ${error.message}`));
          progress.value++;
          continue;
        }
      } else {
        unchanged++;
      }
      selectedBooks.delete(book.path);
      progress.value++;
    }
    if (saved || failed) {
      try { await readLibrary(); }
      catch (error) { $('bulk-errors').append(el('li', error.message)); }
    }
    const summary = `Updated ${saved} · Unchanged ${unchanged} · Failed ${failed}`;
    $('bulk-status').textContent = summary;
    $('bulk-errors').hidden = !$('bulk-errors').childElementCount;
    $('notice').textContent = summary;
  } finally {
    bulkSaving = false;
    $('close-bulk').disabled = false;
    $('close-bulk').textContent = 'Done';
    $('save-bulk').hidden = true;
    setBusy(false);
    render();
  }
};
function formatBytes(bytes) {
  if(!bytes)return '0 B';const units=['B','KB','MB','GB','TB'];const index=Math.min(4,Math.floor(Math.log(bytes)/Math.log(1000)));
  return new Intl.NumberFormat(undefined,{maximumFractionDigits:index>0?1:0}).format(bytes/1000**index)+' '+units[index];
}
function applyStorageLayout() {
  $('storage').classList.toggle('compact',overviewCollapsed);
  $('storage-toggle').setAttribute('aria-expanded',String(!overviewCollapsed));
  const action=overviewCollapsed?'Expand storage overview':'Collapse storage overview';
  $('storage-toggle').setAttribute('aria-label',action);$('storage-toggle').title=action;
  $('storage-details').inert=overviewCollapsed;
}
function animateStorageLayout(){
  const panel=$('storage');
  const moving=[$('storage-size'),$('drive-summary'),$('drive-free'),$('directory-bar'),$('storage-details')].filter(n=>n.getClientRects().length);
  const first=new Map([panel,...moving].map(n=>[n,n.getBoundingClientRect()]));
  for(const animation of storageAnimations)animation.cancel();
  storageAnimations=[];
  applyStorageLayout();
  if(panel.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  const lastHeight=panel.getBoundingClientRect().height;
  const timing={duration:300,easing:'cubic-bezier(.22,1,.36,1)'};
  storageAnimations.push(panel.animate([{height:first.get(panel).height+'px',overflow:'clip'},{height:lastHeight+'px',overflow:'clip'}],timing));
  for(const n of moving){
    if(n===$('storage-details')){
      storageAnimations.push(n.animate(overviewCollapsed?[{opacity:1},{opacity:0}]:[{opacity:0},{opacity:1}],{duration:overviewCollapsed?150:320,easing:'ease-out'}));continue;
    }
    const start=first.get(n),end=n.getBoundingClientRect();
    if(!end.width||!end.height)continue;
    const x=start.left-end.left,y=start.top-end.top;
    const sx=n===$('directory-bar')?start.width/end.width:start.height/end.height;
    const sy=start.height/end.height;
    storageAnimations.push(n.animate([{transformOrigin:'top left',transform:`translate(${x}px,${y}px) scale(${sx},${sy})`},{transformOrigin:'top left',transform:'none'}],timing));
  }
}

function toggleOverview() {
  overviewCollapsed = !overviewCollapsed;
  localStorage.setItem('koshelf-storage-collapsed', String(overviewCollapsed));
  animateStorageLayout();
  animateGenreLayout();
}

$('storage-toggle').onclick = $('genre-toggle').onclick = toggleOverview;

function reviewBackup(review, syncing) {
  const dialog=$('backup-dialog');
  $('backup-heading').textContent=review.count?'Same books, different folders':'Sync from Kindle?';
  $('backup-matches').textContent=review.count
    ? `${review.count} matching EPUB ${review.count===1?'set has':'sets have'} different paths on your Kindle and in the Mac library. How should KoShelf handle them?`
    : 'Copy new and changed Kindle files to your Mac library. Existing copies are kept.';
  dialog.querySelector('details').hidden=!review.count;
  $('backup-options').hidden=!review.count;
  $('backup-move-hint').hidden=!review.count;
  $('confirm-sync').hidden=!syncing||!!review.count;
  const examples=$('backup-examples');examples.replaceChildren();
  for(const group of review.examples){
    const item=el('li');
    item.append(el('div','Kindle · '+group.kindle.join(' · ')),el('div','Backup · '+group.backup.join(' · ')));
    examples.append(item);
  }
  if(review.count>review.examples.length)examples.append(el('li',`Showing ${review.examples.length} of ${review.count} matches.`));
  dialog.querySelector('details').open=false;dialog.returnValue='cancel';
  return new Promise(resolve=>{
    dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true});
    dialog.showModal();
  });
}
// Poll the service's transfer counter while a long sync request is running.
function watchProgress(show) {
  let stopped = false;
  const timer = setInterval(async () => {
    try {
      const response = await shelfRequest('/api/progress');
      if (!response.ok || stopped) return;
      const {label, done, total} = await response.json();
      if (label) show(label, done, total);
    } catch {}
  }, 600);
  return () => { stopped = true; clearInterval(timer); };
}

function syncFeedback(state, title, detail) {
  const feedback=$('sync-feedback');
  feedback.hidden=!state;
  feedback.dataset.state=state;
  $('sync-icon').textContent={success:'✓',warning:'!'}[state]||'';
  $('sync-title').textContent=title||'';
  $('sync-detail').textContent=detail||'';
  $('dismiss-sync').hidden=state==='working';
}
$('dismiss-sync').onclick=()=>syncFeedback('');

function epubComparison(path, comparison) {
  const card = el('article', null, 'sync-version');
  card.append(el('h3', comparison?.title || path), el('p', path, 'sync-version-path'));
  if (!comparison?.available) {
    card.append(el('p', 'KoShelf could not read the book details for both versions.'));
  } else {
    if (comparison.changes.length) {
      const table = el('table', null, 'sync-version-table');
      table.setAttribute('aria-label', 'Changed book details');
      const heading = table.createTHead().insertRow();
      for (const text of ['Detail', 'Kindle', 'Mac']) {
        const column = el('th', text);
        column.scope = 'col';
        heading.append(column);
      }
      const body = table.createTBody();
      for (const change of comparison.changes) {
        const row = body.insertRow();
        const field = el('th', change.label);
        field.scope = 'row';
        row.append(field);
        for (const value of [change.kindle, change.mac]) {
          row.append(el('td', (Array.isArray(value) ? value.join(', ') : value) || '—'));
        }
      }
      card.append(table);
    } else {
      card.append(el('p', 'The displayed book details match.'));
    }
    if (comparison.contentsDiffer) {
      card.append(el('p', 'Other EPUB files also differ, such as the text, cover, or formatting.'));
    } else if (!comparison.changes.length) {
      card.append(el('p', 'The versions differ in other metadata or EPUB packaging.'));
    }
  }
  const label = el('label', null, 'sync-version-choice');
  const checkbox = el('input');
  checkbox.type = 'checkbox';
  checkbox.value = path;
  checkbox.setAttribute('aria-label', `Use Mac version on Kindle for ${path}`);
  label.append(checkbox, document.createTextNode('Use Mac version on Kindle'));
  card.append(label);
  return card;
}

function reviewSync(preview) {
  const dialog = $('sync-dialog');
  const sections = [
    ['sync-to-mac', preview.toMac, 'Kindle → Mac · New files', preview.toMacPaths],
    ['sync-to-kindle', preview.toKindle, 'Mac → Kindle · New EPUBs', preview.toKindlePaths],
    ['sync-mac-newer', (preview.macNewerPaths || []).length, 'Mac → Kindle · Edited on your Mac', preview.macNewerPaths || []],
    ['sync-conflict-count', preview.versionDifferenceCount ?? preview.conflicts, 'Different versions at the same path', preview.versionDifferencePaths || preview.conflictPaths]
  ];
  for (const [id, count, label, paths] of sections) {
    $(id).textContent = `${label} (${count})`;
    const list = $(id.replace(/-count$/, '') + '-paths');
    list.replaceChildren(...paths.map(path => el('li', path)));
    if (count > paths.length) list.append(el('li', `And ${count - paths.length} more`));
    list.parentElement.open = false;
  }
  const moveGroups = preview.moveGroups || [];
  $('sync-folders').hidden = !moveGroups.length;
  $('sync-folder-count').textContent = `Same book, different name or folder (${moveGroups.length})`;
  $('sync-move-choices').replaceChildren(...moveGroups.map(syncMoveChoice));
  const progress = preview.readingProgress || [];
  $('sync-books-note').hidden = preview.appleBooks !== 'needs-access';
  $('sync-reading-progress').hidden = !progress.length;
  $('sync-reading-progress').open = true;
  $('sync-reading-progress-count').textContent = `Reading progress (${progress.length})`;
  $('sync-reading-progress-paths').replaceChildren(...progress.map(readingProgressChange));
  $('sync-mac-newer-group').hidden = !(preview.macNewerPaths || []).length;
  const epubConflicts = preview.epubConflictPaths || [];
  const choices = $('sync-use-mac');
  choices.replaceChildren();
  for (const path of epubConflicts) {
    choices.append(epubComparison(path, preview.epubComparisons?.[path]));
  }
  $('sync-conflicts').hidden = !(preview.conflicts || epubConflicts.length);
  $('sync-replacement-hint').hidden = !epubConflicts.length;
  const removals = preview.removeEmptyFolders || [];
  const incomingFolders = preview.incomingEmptyFolders || [];
  $('sync-empty-folders').hidden = !(removals.length || incomingFolders.length);
  $('sync-empty-folders').open = true;
  $('sync-empty-folder-count').textContent = `Empty folder changes (${removals.length + incomingFolders.length})`;
  $('sync-empty-folder-paths').replaceChildren(...removals.map(path => el('li', `Remove from Kindle · ${path}`)),
    ...incomingFolders.map(path => el('li', `Copy to Mac · ${path}`)));
  const statuses = preview.readingStatuses || [];
  $('sync-reading-status').hidden = !statuses.length;
  $('sync-reading-status').open = true;
  $('sync-reading-count').textContent = `KOReader status changes (${statuses.length})`;
  $('sync-reading-paths').replaceChildren(...statuses.map(({path, status}) =>
    el('li', `${path} · ${readingLabels[status]}`)));
  const archive = preview.archiveOnKindle || [];
  $('sync-duplicate-archive').hidden = !archive.length;
  $('sync-archive-count').textContent = `Duplicates to archive from Kindle (${archive.length})`;
  $('sync-archive-paths').replaceChildren(...archive.map(({remove, keep}) => {
    const item = el('li');
    item.append(el('div', `Archive · ${remove}`), el('div', `Keep · ${keep}`));
    return item;
  }));
  $('apply-sync').hidden = !(removals.length || incomingFolders.length || statuses.length || archive.length || preview.toMac || preview.toKindle || preview.count || preview.conflicts || epubConflicts.length || (preview.macNewerPaths || []).length || progress.length);
  updateMoveChoices();
  dialog.returnValue = 'cancel';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'apply'
      ? {choice: preview.count ? 'align' : 'copy', moves: chosenMoves(),
         useMac: Array.from(choices.querySelectorAll('input:checked'), input => input.value)}
      : null), {once: true});
    dialog.showModal();
  });
}

function percentLabel(fraction) {
  if (fraction >= 1) return 'finished';
  const percent = fraction * 100;
  return percent > 0 && percent < 1 ? '<1%' : `${Math.min(99, Math.round(percent))}%`;
}

function progressRow(place, role, from, to) {
  // A receiving bar shows its current progress muted, and the part sync adds highlighted.
  const row = el('div', null, `sync-progress-row ${role}`);
  const name = el('span', place, 'sync-progress-place');
  name.append(el('small', role === 'source' ? 'sends' : 'receives'));
  const track = el('span', null, 'book-reading-track');
  track.setAttribute('aria-hidden', 'true');
  const current = el('span', null, 'book-reading-fill');
  current.style.width = `${Math.min(from, 1) * 100}%`;
  track.append(current);
  if (role === 'receiver' && to > from) {
    row.classList.add('gains');
    const gain = el('span', null, 'sync-progress-gain');
    gain.style.left = `${from * 100}%`;
    gain.style.width = `${(Math.min(to, 1) - from) * 100}%`;
    track.append(gain);
  }
  const value = role === 'source' || to === from ? percentLabel(from) : `${percentLabel(from)} → ${percentLabel(to)}`;
  row.append(name, track, el('span', value, 'book-reading-percent'));
  return row;
}

function readingProgressChange({book, winner, kindle, mac, books, note}) {
  const [source, receiver, sent, had] = winner === 'books' ? ['Apple Books', 'Kindle', books, kindle]
    : winner === 'kindle' ? ['Kindle', 'Mac', kindle, mac] : ['Mac', 'Kindle', mac, kindle];
  const card = el('article', null, 'sync-progress');
  card.setAttribute('aria-label', sent === had
    ? `${book}: both at ${percentLabel(sent)}. The ${receiver} copy gets the ${source} reading file.`
    : `${book}: the ${source} at ${percentLabel(sent)} updates the ${receiver} from ${percentLabel(had)}.`);
  const heading = el('div', null, 'sync-progress-heading');
  heading.append(el('strong', book), el('span', `${source} → ${receiver}`, 'sync-progress-direction'));
  const arrow = el('span', '↓', 'sync-progress-arrow');
  arrow.setAttribute('aria-hidden', 'true');
  card.append(heading, progressRow(source, 'source', sent, sent), arrow, progressRow(receiver, 'receiver', had, sent));
  if (note === 'books') {
    card.append(el('p', 'Approximate: the Kindle opens at Apple Books’ percentage, which may be a page or two off. Its highlights and notes stay, and the Mac copy is updated to match.', 'hint'));
  } else if (note === 'books-new') {
    card.append(el('p', 'Approximate: the Kindle opens at Apple Books’ percentage, which may be a page or two off. The Mac gets this reading file at the next sync.', 'hint'));
  } else if (note === 'books-finished') {
    card.append(el('p', 'Finished in Apple Books. Only the finished status goes to the Kindle. Its reading position, highlights, and notes stay.', 'hint'));
  } else if (note === 'finished') {
    card.append(el('p', 'Only the finished status goes to the Kindle. Its reading position, highlights, and notes stay.', 'hint'));
  } else if (note === 'mac-ahead') {
    card.append(el('p', 'The Mac copy shows more progress, but KoShelf can only carry a finished status to the Kindle. The Kindle’s reading position, highlights, and notes are kept.', 'hint'));
  } else if (sent === had) {
    card.append(el('p', 'Same progress. Notes or settings differ, so the Kindle copy is used.', 'hint'));
  }
  return card;
}

function syncMoveChoice(group) {
  const card = el('article', null, 'sync-version');
  const heading = el('h3', group.title || group.mac[0]);
  heading.id = `move-title-${group.id}`;
  card.append(heading);
  card.setAttribute('role', 'radiogroup');
  card.setAttribute('aria-labelledby', heading.id);
  if (!group.single) {
    card.append(el('p', `Several copies exist (Kindle: ${group.kindle.join(' · ')} / Mac: ${group.mac.join(' · ')}). They sync as separate files.`));
    return card;
  }
  const options = [
    ['backup', 'Use the Mac name', `Renames the Kindle copy to ${group.mac[0]}`],
    ['kindle', 'Use the Kindle name', `Renames the Mac copy to ${group.kindle[0]}`],
    ['both', 'Keep both for now', 'Leaves both names as they are this time. The files are identical, so sync asks again until you change the title or author of the Mac copy in KoShelf.'],
  ];
  for (const [value, title, detail] of options) {
    const label = el('label', null, 'sync-move-option');
    const radio = el('input');
    radio.type = 'radio';
    radio.name = `move-${group.id}`;
    radio.value = value;
    radio.dataset.group = group.id;
    radio.onchange = updateMoveChoices;
    const text = el('span', title);
    text.append(el('small', detail));
    label.append(radio, text);
    card.append(label);
  }
  return card;
}

function chosenMoves() {
  const moves = {};
  for (const input of $('sync-move-choices').querySelectorAll('input:checked')) {
    moves[input.dataset.group] = input.value;
  }
  return moves;
}

function updateMoveChoices() {
  // Every matching book needs a choice before syncing.
  const cards = [...$('sync-move-choices').querySelectorAll('.sync-version')].filter(card => card.querySelector('input'));
  const ready = cards.every(card => card.querySelector('input:checked'));
  $('apply-sync').disabled = !ready;
  $('apply-sync').title = ready ? '' : 'Choose a name for each matching book first.';
}

$('backup').onclick=async()=>{
  if(busy||loading||!library.connected)return;
  const workflow = usbWorkflow();
  if (workflow === 'send') {
    if (selectedBooks.size) $('send-selected').click();
    else {
      if (!selectionMode) $('select-books').click();
      $('notice').textContent = 'Select the books you want to send, then choose Send to Kindle.';
    }
    return;
  }
  if (workflow === 'setup') {
    setBusy(true);
    try {
      await mutation('/api/kindle-setup', {device: device.usbKindle.device}, {updateStorage: false});
      await checkConnection();
      $('notice').textContent = 'The KOReader books folder is ready. Choose Sync libraries to send EPUBs.';
    } catch (error) {
      $('notice').textContent = error.message;
    } finally {
      setBusy(false);
    }
    return;
  }
  const kindle=library.localFirst?device.kindle:null;
  const endpoint=kindle?'/api/sync':'/api/backup';
  const payload=kindle?{kindle}:{};
  const action=kindle?'Sync':'Backup';
  setBusy(true);
  $('notice').textContent='';
  syncFeedback('working',kindle?'Checking libraries…':'Backing up library…','Keep your library connected.');
  let choice;
  let applied=false;
  let outcome;
  try{
    let r=await mutation(endpoint,payload);
    if(r.review){
      syncFeedback('');
      const selection = kindle ? await reviewSync(r) : {choice: await reviewBackup(r,false)};
      choice = selection?.choice;
      if(!['kindle','backup','merge','copy','align'].includes(choice))return;
      syncFeedback('working',kindle?'Syncing libraries…':'Backing up library…','Keep your library connected until this finishes.');
      applied=true;
      const stopProgress=watchProgress((label,done,total)=>{
        $('sync-detail').textContent=`${label} · ${total?`${Math.min(done+1,total)} of ${total}`:`${done} files`} · Keep your library connected.`;
      });
      try{r=await mutation(endpoint,{...payload,...selection,review:r.review});}
      finally{stopProgress();}
    }
    const incoming = kindle ? r.mac : r;
    const outgoing = kindle ? r.kindle : null;
    const failed = incoming.failed + (outgoing?.failed || 0);
    const summary = outgoing
      ? `${incoming.copied} copied to Mac · ${outgoing.copied} copied to Kindle`
      : `${incoming.copied} copied · ${incoming.skipped} already backed up`;
    const details = [
      summary,
      outgoing?.readingProgress && `${outgoing.readingProgress} reading positions updated`,
      outgoing?.readingStatuses && `${outgoing.readingStatuses} KOReader statuses updated`,
      outgoing?.removedEmptyFolders && `${outgoing.removedEmptyFolders} empty Kindle folders removed`,
      outgoing?.archived && `${outgoing.archived} Kindle duplicates archived`,
      outgoing?.replaced && `${outgoing.replaced} Kindle EPUBs updated from Mac`,
      incoming.moved && `${incoming.moved} moved`,
      incoming.versions && `${incoming.versions} changed versions kept`,
      failed && `${failed} failed. ${[...incoming.errors, ...(outgoing?.errors || [])].join(' · ')}`
    ].filter(Boolean).join(' · ');
    outcome = [failed ? 'warning' : 'success', action + (failed ? ' finished with errors' : ' complete'), details];
    if((kindle&&applied)||choice==='backup'){
      syncFeedback('working','Refreshing library…','Updating the bookshelf with your changes.');
      lastMove=null;$('undo').hidden=true;
      await readLibrary();
      comparison=null;comparedAt=0;
      await checkConnection().catch(()=>{});
    }
  }catch(err){
    outcome=outcome
      ? ['warning',outcome[1],outcome[2]+' · The library view could not refresh. '+err.message]
      : ['warning',action+' could not finish',err.message];
  }finally{
    setBusy(false);render();
    if(outcome)syncFeedback(...outcome);
  }
};
function renderStorage() {
  applyStorageLayout();
  const storage=library.storage;const current=folder||'';const stat=storage?.folders?.[current];
  $('storage').hidden=!library.connected||!stat;if(!stat)return;
  $('storage-size').textContent=formatBytes(stat.bytes);
  $('storage-caption').textContent=`${current||'Your books folder'} · ${stat.files} file${stat.files===1?'':'s'}, including subfolders`;
  const volume=storage.volume;document.querySelector('.storage-drive').hidden=!volume;
  if(volume){$('drive-summary').textContent=`${formatBytes(volume.used)} of ${formatBytes(volume.total)} used`;$('drive-fill').style.width=(volume.total?Math.min(100,volume.used/volume.total*100):0)+'%';$('drive-free').textContent=formatBytes(volume.free)+' available on '+(library.canEject?'your Kindle':'this volume');}
  const prefix=current?current+'/':'';
  let groups=Object.entries(storage.folders).filter(([path])=>path!==current&&path.startsWith(prefix)&&!path.slice(prefix.length).includes('/')&&!path.slice(prefix.length).startsWith('.')&&!path.toLowerCase().endsWith('.sdr')).map(([path,value])=>({path,label:path.slice(prefix.length),bytes:value.bytes})).sort((a,b)=>b.bytes-a.bytes||a.label.localeCompare(b.label));
  const visibleBytes=groups.reduce((sum,g)=>sum+g.bytes,0);
  const supporting=Math.max(0,stat.bytes-stat.direct-visibleBytes);
  const others=groups.slice(6);groups=groups.slice(0,6);
  if(others.length)groups.push({label:`${others.length} more folders`,bytes:others.reduce((sum,g)=>sum+g.bytes,0)});
  if(stat.direct)groups.push({label:'Books & files here',bytes:stat.direct});
  if(supporting)groups.push({label:'Companion & hidden folders',bytes:supporting});
  const colors=shelfColors;
  $('directory-bar').replaceChildren();$('directory-breakdown').replaceChildren();
  groups.forEach((g,i)=>{
    const color=colors[i%colors.length];
    if(g.bytes){const segment=el('div');segment.style.width=(stat.bytes?g.bytes/stat.bytes*100:0)+'%';segment.style.backgroundColor=color;segment.title=g.label+' · '+formatBytes(g.bytes);$('directory-bar').append(segment);}
    const item=el(g.path?'button':'div',null,'storage-item');const dot=el('i');dot.style.backgroundColor=color;item.append(dot,el('span',g.label),el('strong',formatBytes(g.bytes)));
    if(g.path){item.title='Open '+g.path;item.onclick=()=>{if(!busy){folder=g.path;$('search').value='';render();}};}
    $('directory-breakdown').append(item);
  });
  $('storage-note').textContent=(storage.partial?'Some files could not be counted. ':'')+'Folder sizes include all file types, hidden files and KOReader data. File sizes may differ from disk space used.';
}
loadSettings().finally(refresh);

const fontDialog = $('font-dialog');
const fontReader = $('font-reader');
const fontTarget = $('font-target');
const fontStatus = $('font-status');
const fontList = $('font-list');
const fontSample = $('font-sample');
let fontState = {device: null, readers: {}};
let fontWorking = false;
let fontPreview = null;
let fontPreviewRequest = 0;
const readerLabels = {kindle: 'Kindle reader', koreader: 'KOReader'};

async function localRequest(path, options) {
  const response = await shelfRequest(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'KoShelf could not complete this request.');
  return data;
}

function clearFontSample() {
  fontPreviewRequest++;
  if (fontPreview) document.fonts.delete(fontPreview);
  fontPreview = null;
  fontSample.hidden = true;
}

async function previewFont(reader, entry) {
  clearFontSample();
  const request = fontPreviewRequest;
  const query = new URLSearchParams({device: fontState.device, reader, path: entry.path, version: entry.version});
  try {
    const response = await shelfRequest('/api/font-preview?' + query);
    if (!response.ok) throw Error('Font unavailable.');
    const face = await new FontFace('KoShelfFontSample', await response.arrayBuffer()).load();
    if (request !== fontPreviewRequest || !fontDialog.open) return;
    document.fonts.add(face);
    fontPreview = face;
    fontSample.textContent = 'The quick brown fox jumps over the lazy dog. 0123456789';
    fontSample.hidden = false;
    fontSample.scrollIntoView({block: 'nearest'});
  } catch {
    if (request === fontPreviewRequest) fontStatus.textContent = 'This font could not be previewed.';
  }
}

function renderFonts(expandedFamily) {
  fontList.replaceChildren();
  const reader = fontReader.value;
  const search = $('font-search').value.trim().toLocaleLowerCase();
  const families = new Map();
  for (const entry of fontState.readers[reader] || []) {
    if (!entry.family.toLocaleLowerCase().includes(search)) continue;
    if (!families.has(entry.family)) families.set(entry.family, []);
    families.get(entry.family).push(entry);
  }
  for (const [family, entries] of families) {
    const group = el('details', null, 'font-family');
    group.open = family === expandedFamily;
    const heading = el('summary');
    heading.append(el('span', family), el('small', entries.length === 1 ? '1 file' : entries.length + ' files'));
    group.append(heading);
    for (const entry of entries) {
      const row = el('div', null, 'font-file');
      const title = el('div');
      title.append(el('strong', entry.style + (entry.variable ? ' · Variable' : '')),
        el('small', entry.path));
      row.append(title);
      if (entry.preview) {
        const preview = el('button', 'Preview', 'button');
        preview.type = 'button';
        preview.onclick = () => previewFont(reader, entry);
        row.append(preview);
      }
      if (entry.removable) {
        const remove = el('button', 'Remove…', 'button');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + family + ' ' + entry.style);
        remove.onclick = () => {
          $('font-remove-message').textContent =
            `Remove ${family} ${entry.style} from ${readerLabels[reader]}? A copy will be saved on this Mac.`;
          $('font-remove-dialog').showModal();
          $('confirm-font-removal').onclick = () => {
            $('font-remove-dialog').close();
            void runFontTask('Removing font…', async () => {
              const result = await localRequest('/api/font-remove', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({device: fontState.device, reader, path: entry.path, version: entry.version})
              });
              return 'Font removed. Recovery copy:\n' + result.backup;
            }, family);
          };
        };
        row.append(remove);
      } else row.append(el('small', 'Bundled'));
      group.append(row);
    }
    fontList.append(group);
  }
  if (!families.size) fontList.append(el('p', search ? 'No font families match.' : 'No fonts installed here.'));
}

async function loadFonts(message = '', expandedFamily) {
  clearFontSample();
  const state = await localRequest('/api/fonts');
  if (!fontDialog.open) return;
  fontState = state;
  $('font-controls').hidden = !state.device;
  if (!state.device) {
    fontList.replaceChildren();
    fontStatus.textContent = 'Connect your Kindle by USB to manage its fonts.';
    return;
  }
  const hasKOReader = !!state.readers.koreader;
  fontReader.options[1].disabled = !hasKOReader;
  for (const option of fontTarget.options) option.disabled = !hasKOReader && option.value !== 'kindle';
  if (fontReader.selectedOptions[0].disabled) fontReader.value = 'kindle';
  if (fontTarget.selectedOptions[0].disabled) fontTarget.value = 'kindle';
  fontStatus.textContent = message || 'Kindle connected.';
  renderFonts(expandedFamily);
}

async function runFontTask(progress, task, expandedFamily) {
  if (fontWorking) return;
  fontWorking = true;
  fontStatus.textContent = progress;
  $('font-controls').inert = true;
  $('close-font-manager').disabled = true;
  $('refresh-fonts').disabled = true;
  try {
    const result = await task();
    await loadFonts(result, expandedFamily);
  } catch (error) {
    fontStatus.textContent = error.message;
  } finally {
    $('font-controls').inert = false;
    $('close-font-manager').disabled = false;
    $('refresh-fonts').disabled = false;
    fontWorking = false;
  }
}

function importFonts(files) {
  if (!fontState.device || busy || fontWorking || !files.length) return;
  const readers = fontTarget.value === 'both' ? ['kindle', 'koreader'] : [fontTarget.value];
  void runFontTask('Installing fonts…', async () => {
    let installed = 0;
    let duplicates = 0;
    const errors = [];
    for (const file of files) {
      for (const reader of readers) {
        try {
          const query = new URLSearchParams({device: fontState.device, reader, name: file.name});
          const data = await localRequest('/api/font-install?' + query, {
            method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
          });
          if (data.result === 'Installed') installed++;
          else duplicates++;
        } catch (error) {
          errors.push(file.name + ' (' + readerLabels[reader] + ') · ' + error.message);
        }
      }
    }
    return [`${installed} installed.`, duplicates ? `${duplicates} already present.` : '', ...errors].filter(Boolean).join('\n');
  });
}

$('open-font-manager').onclick = () => {
  if (busy || loading) return;
  shelfActions.open = false;
  $('font-controls').hidden = true;
  $('font-search').value = '';
  fontDialog.showModal();
  void refreshFonts();
};
function refreshFonts() {
  return runFontTask('Reading installed fonts…', () => '');
}
$('refresh-fonts').onclick = refreshFonts;
fontDialog.addEventListener('cancel', event => { if (fontWorking) event.preventDefault(); });
fontDialog.addEventListener('close', clearFontSample);
fontReader.onchange = () => { clearFontSample(); renderFonts(); };
$('font-search').oninput = () => { clearFontSample(); renderFonts(); };
$('choose-font-files').onclick = () => $('font-files').click();
$('font-files').onchange = event => {
  importFonts(Array.from(event.target.files));
  event.target.value = '';
};
$('font-drop').addEventListener('dragover', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = fontWorking ? 'none' : 'copy';
  $('font-drop').classList.toggle('drop-over', !fontWorking);
});
$('font-drop').addEventListener('dragleave', event => {
  if (!$('font-drop').contains(event.relatedTarget)) $('font-drop').classList.remove('drop-over');
});
$('font-drop').addEventListener('drop', event => {
  if (!isFileDrop(event)) return;
  event.preventDefault();
  event.stopPropagation();
  $('font-drop').classList.remove('drop-over');
  importFonts(Array.from(event.dataTransfer.files));
});

const deliveryDialog = $('kindle-delivery-dialog');
const deliveryStatus = $('kindle-delivery-status');
const deliveryConfirm = $('confirm-kindle-delivery');
const deliveryRefresh = $('refresh-kindle-delivery');
let deliveryReview = null;

function openKindleDelivery(books) {
  if (busy || loading || !books.length) return;
  deliveryReview = {books, root: library.root, device: null};
  $('kindle-delivery-books').replaceChildren(...books.map(book => el('li', book.title)));
  $('kindle-delivery-results').replaceChildren();
  $('kindle-delivery-progress').hidden = true;
  deliveryConfirm.hidden = false;
  deliveryRefresh.hidden = false;
  deliveryDialog.showModal();
  void checkKindleDelivery();
}

$('send-selected').onclick = () => openKindleDelivery(library.books.filter(book => selectedBooks.has(book.path)));

async function checkKindleDelivery() {
  const review = deliveryReview;
  review.device = null;
  review.ready = false;
  deliveryConfirm.disabled = true;
  deliveryRefresh.disabled = true;
  deliveryStatus.textContent = 'Checking USB connection and converter…';
  try {
    const data = await localRequest('/api/kindle-delivery');
    if (review !== deliveryReview || !deliveryDialog.open) return;
    review.device = data.device;
    review.sandboxed = data.sandboxed !== false;
    $('kindle-delivery-warning').hidden = !data.converter || review.sandboxed;
    $('kindle-delivery-consent').hidden = !data.converter || review.sandboxed;
    $('kindle-delivery-unsandboxed').checked = false;
    if (!data.converter) {
      deliveryStatus.textContent = 'KoShelf’s converter is missing. Reinstall KoShelf or build the converter from source.';
    } else if (!data.device) {
      deliveryStatus.textContent = 'Connect one Kindle that appears as a drive on this computer. This USB workflow needs access to its documents folder.';
    } else {
      deliveryStatus.textContent = `Ready to send to ${data.name}. Existing files with the same name will be skipped.`;
      review.ready = true;
      deliveryConfirm.disabled = !review.sandboxed && !$('kindle-delivery-unsandboxed').checked;
    }
  } catch (error) {
    if (review === deliveryReview) deliveryStatus.textContent = error.message;
  } finally {
    if (review === deliveryReview) deliveryRefresh.disabled = false;
  }
}

deliveryRefresh.onclick = checkKindleDelivery;
$('kindle-delivery-unsandboxed').onchange = () => {
  // Without a sandbox, each conversion needs the box ticked.
  if (deliveryReview?.ready && !busy) deliveryConfirm.disabled = !$('kindle-delivery-unsandboxed').checked;
};
deliveryDialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });

deliveryConfirm.onclick = async () => {
  const review = deliveryReview;
  if (busy || !review?.device) return;
  setBusy(true);
  deliveryConfirm.disabled = true;
  deliveryRefresh.hidden = true;
  $('close-kindle-delivery').disabled = true;
  const progress = $('kindle-delivery-progress');
  const results = $('kindle-delivery-results');
  progress.max = review.books.length;
  progress.value = 0;
  progress.hidden = false;
  let sent = 0;
  let skipped = 0;
  let failed = 0;
  try {
    for (const book of review.books) {
      deliveryStatus.textContent = `Converting and sending ${progress.value + 1} of ${progress.max} · ${book.title}`;
      try {
        const result = await localRequest('/api/kindle-delivery', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({device: review.device, library: review.root, path: book.path,
                                allowUnsandboxed: !review.sandboxed && $('kindle-delivery-unsandboxed').checked})
        });
        if (result.result === 'sent') {
          sent++;
          results.append(el('li', 'Sent · ' + result.name));
        } else {
          skipped++;
          results.append(el('li', 'File already exists · ' + result.name));
        }
      } catch (error) {
        failed++;
        results.append(el('li', book.title + ' · ' + error.message));
      }
      progress.value++;
    }
    deliveryStatus.textContent = `Finished · ${sent} sent, ${skipped} skipped, ${failed} failed.` +
      (sent ? ' You can now eject your Kindle.' : '');
  } finally {
    deliveryConfirm.hidden = true;
    $('close-kindle-delivery').disabled = false;
    setBusy(false);
  }
};
