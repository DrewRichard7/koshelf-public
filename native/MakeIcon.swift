import AppKit
let size = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let rect = NSRect(x: 60, y: 60, width: 904, height: 904)
NSColor(calibratedRed: 0.90, green: 0.93, blue: 0.87, alpha: 1).setFill()
NSBezierPath(roundedRect: rect, xRadius: 200, yRadius: 200).fill()
let dark = NSColor(calibratedRed: 0.20, green: 0.34, blue: 0.27, alpha: 1)
dark.setFill()
NSBezierPath(roundedRect: NSRect(x: 245, y: 270, width: 100, height: 460), xRadius: 15, yRadius: 15).fill()
NSBezierPath(roundedRect: NSRect(x: 380, y: 270, width: 100, height: 510), xRadius: 15, yRadius: 15).fill()
NSGraphicsContext.saveGraphicsState()
let transform = NSAffineTransform()
transform.translateX(by: 565, yBy: 280)
transform.rotate(byDegrees: 12)
transform.concat()
NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: 110, height: 410), xRadius: 15, yRadius: 15).fill()
NSGraphicsContext.restoreGraphicsState()
NSBezierPath(roundedRect: NSRect(x: 205, y: 220, width: 610, height: 32), xRadius: 16, yRadius: 16).fill()
image.unlockFocus()
let directory = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
for n in [16,32,64,128,256,512,1024] {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: n, pixelsHigh: n, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: n, height: n))
    NSGraphicsContext.restoreGraphicsState()
    let names = n == 16 ? ["icon_16x16"] : n == 32 ? ["icon_16x16@2x","icon_32x32"] : n == 64 ? ["icon_32x32@2x"] : n == 128 ? ["icon_128x128"] : n == 256 ? ["icon_128x128@2x","icon_256x256"] : n == 512 ? ["icon_256x256@2x","icon_512x512"] : ["icon_512x512@2x"]
    for name in names { try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: directory+"/"+name+".png")) }
}
