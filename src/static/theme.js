// Sets light or dark before the page draws, so it never flashes the wrong one. The server puts the
// Appearance setting in data-appearance ("system" follows the system) and the theme in data-palette.
(() => {
  const root = document.documentElement;
  const systemDark = matchMedia('(prefers-color-scheme: dark)');
  const apply = () => {
    const choice = root.dataset.appearance || 'system';
    root.dataset.theme = choice === 'system' ? (systemDark.matches ? 'dark' : 'light') : choice;
    // In the app, the window's title bar and background follow the same choice.
    window.webkit?.messageHandlers?.appearance?.postMessage(choice);
  };
  apply();
  systemDark.addEventListener('change', apply);
  window.koshelfSetAppearance = choice => { root.dataset.appearance = choice; apply(); };
  window.koshelfSetTheme = name => { root.dataset.palette = name; };
})();
