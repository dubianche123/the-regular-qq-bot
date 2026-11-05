// Maintainer-only mask/compositing utility. No model call and no runtime dependency.
import Foundation
import CoreGraphics
import ImageIO

struct Raster {
    let width: Int
    let height: Int
    let colorSpace: CGColorSpace
    var pixels: [UInt8]
}

enum Failure: Error { case invalid(String) }

func load(_ path: String) throws -> Raster {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0,
              [kCGImageSourceShouldCache: false] as CFDictionary),
          image.bitsPerComponent == 8,
          let sourceData = image.dataProvider?.data else {
        throw Failure.invalid("Cannot decode an 8-bit PNG")
    }
    let bytes = sourceData as Data
    let channels = image.bitsPerPixel / 8
    guard (channels == 4 && image.alphaInfo == .last)
            || (channels == 3 && image.alphaInfo == .none) else {
        throw Failure.invalid("PNG must provide straight RGB or RGBA pixels")
    }
    var pixels = [UInt8](repeating: 255, count: image.width * image.height * 4)
    for y in 0..<image.height {
        for x in 0..<image.width {
            let src = y * image.bytesPerRow + x * channels
            let dst = (y * image.width + x) * 4
            pixels[dst] = bytes[src]
            pixels[dst + 1] = bytes[src + 1]
            pixels[dst + 2] = bytes[src + 2]
            if channels == 4 { pixels[dst + 3] = bytes[src + 3] }
        }
    }
    return Raster(width: image.width, height: image.height,
        colorSpace: image.colorSpace ?? CGColorSpace(name: CGColorSpace.sRGB)!, pixels: pixels)
}

func save(_ raster: Raster, _ path: String) throws {
    let data = Data(raster.pixels) as CFData
    guard let provider = CGDataProvider(data: data),
          let image = CGImage(width: raster.width, height: raster.height,
            bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: raster.width * 4,
            space: raster.colorSpace,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
          let destination = CGImageDestinationCreateWithURL(
            URL(fileURLWithPath: path) as CFURL, "public.png" as CFString, 1, nil) else {
        throw Failure.invalid("Cannot create output PNG")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw Failure.invalid("Cannot finish output PNG")
    }
}

func smooth(_ value: Double) -> Double {
    let t = min(1, max(0, value)); return t * t * (3 - 2 * t)
}

// A tiny lower-limb mesh displacement preserves the original costume details.
// The head/body and pixels above both knees remain byte-identical.
func dangling(_ original: Raster, amount: Double) -> Raster {
    var output = original
    func delta(_ x: Double, _ y: Double) -> (Double, Double) {
        let t = smooth((y - 1160) / 230)
        let minimumX = 390 - 100 * smooth((y - 1210) / 90)
        let support = smooth((x - minimumX) / 30)
        let angle = 0.065 * amount
        let dx = (cos(angle)-1) * (x-500) - sin(angle) * (y-1160)
        let dy = sin(angle) * (x-500) + (cos(angle)-1) * (y-1160) - 6*amount
        return (t * support * dx, t * support * dy)
    }
    for y in 1160..<original.height {
        for x in 0..<original.width {
            var sx = Double(x), sy = Double(y)
            for _ in 0..<6 {
                let d = delta(sx, sy); sx = Double(x) - d.0; sy = Double(y) - d.1
            }
            let target = (y * original.width + x) * 4
            if sx < 0 || sy < 0 || sx >= Double(original.width-1) || sy >= Double(original.height-1) {
                for channel in 0..<4 { output.pixels[target+channel] = 0 }; continue
            }
            let ix = Int(sx), iy = Int(sy), tx = sx - Double(ix), ty = sy - Double(iy)
            for channel in 0..<4 {
                let a = Double(original.pixels[(iy*original.width+ix)*4+channel])
                let b = Double(original.pixels[(iy*original.width+ix+1)*4+channel])
                let c = Double(original.pixels[((iy+1)*original.width+ix)*4+channel])
                let d = Double(original.pixels[((iy+1)*original.width+ix+1)*4+channel])
                output.pixels[target+channel] = UInt8(((a*(1-tx)+b*tx)*(1-ty) + (c*(1-tx)+d*tx)*ty).rounded())
            }
        }
    }
    return output
}

struct Ellipse {
    let x: Double
    let y: Double
    let rx: Double
    let ry: Double

    func weight(_ px: Int, _ py: Int) -> Double {
        let dx = (Double(px) + 0.5 - x) / rx
        let dy = (Double(py) + 0.5 - y) / ry
        let distance = sqrt(dx * dx + dy * dy)
        if distance >= 1 { return 0 }
        if distance <= 0.80 { return 1 }
        let t = (1 - distance) / 0.20
        return t * t * (3 - 2 * t)
    }
}

struct Vertex { let x: Double; let y: Double }
enum Region {
    case ellipse(Ellipse)
    case polygon([Vertex])

    func weight(_ px: Int, _ py: Int) -> Double {
        return weightAt(Double(px) + 0.5, Double(py) + 0.5)
    }

    func weightAt(_ x: Double, _ y: Double, feather: Double = 4) -> Double {
        switch self {
        case let .ellipse(value): return value.weight(Int(x), Int(y))
        case let .polygon(points):
            var inside = false, nearest = Double.greatestFiniteMagnitude
            for index in points.indices {
                let a = points[index], b = points[(index + 1) % points.count]
                if (a.y > y) != (b.y > y),
                   x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x { inside.toggle() }
                let dx = b.x - a.x, dy = b.y - a.y
                let t = max(0, min(1, ((x-a.x)*dx + (y-a.y)*dy) / (dx*dx + dy*dy)))
                nearest = min(nearest, hypot(x - (a.x + t*dx), y - (a.y + t*dy)))
            }
            if !inside { return 0 }
            let t = min(1, nearest / feather)
            return t * t * (3 - 2 * t)
        }
    }
}

let eyes = [Ellipse(x: 458, y: 270, rx: 58, ry: 35),
            Ellipse(x: 563, y: 238, rx: 52, ry: 35)]
let mouth = Ellipse(x: 530, y: 304, rx: 32, ry: 28)

func areas(_ pose: String) throws -> [Region] {
    switch pose {
    case "blink": return eyes.map(Region.ellipse)
    case "thinking", "celebrate", "sleeping": return (eyes + [mouth]).map(Region.ellipse)
    case "wave": return [.polygon([
        Vertex(x: 140, y: 250), Vertex(x: 360, y: 250), Vertex(x: 360, y: 340),
        Vertex(x: 390, y: 390), Vertex(x: 435, y: 400), Vertex(x: 475, y: 420), Vertex(x: 490, y: 490),
        Vertex(x: 470, y: 590), Vertex(x: 420, y: 640), Vertex(x: 300, y: 650),
        Vertex(x: 215, y: 560), Vertex(x: 170, y: 420), Vertex(x: 140, y: 380)])]
    case "wave-mid": return [.polygon([
        Vertex(x: 185, y: 275), Vertex(x: 355, y: 275), Vertex(x: 355, y: 345),
        Vertex(x: 375, y: 385), Vertex(x: 405, y: 420), Vertex(x: 405, y: 480),
        Vertex(x: 275, y: 495), Vertex(x: 230, y: 410), Vertex(x: 185, y: 380)])]
    default: throw Failure.invalid("Unknown expression")
    }
}

func run() throws {
    let args = CommandLine.arguments
    guard args.count >= 4 else {
        throw Failure.invalid("Usage: mascot_expression mask ORIGINAL OUT POSE; composite ORIGINAL EDIT OUT POSE")
    }
    let mode = args[1]
    let original = try load(args[2])
    guard original.width == 1024 && original.height == 1536 else {
        throw Failure.invalid("Original must be 1024x1536")
    }
    if mode == "deform", args.count == 5, let amount = Double(args[4]), amount >= 0, amount <= 1 {
        try save(dangling(original, amount: amount), args[3]); return
    }
    if mode == "preview", args.count == 4 {
        var output = original
        for index in stride(from: 0, to: output.pixels.count, by: 4) {
            let alpha = Double(original.pixels[index + 3]) / 255
            for channel in 0..<3 {
                output.pixels[index + channel] = UInt8((Double(original.pixels[index + channel]) * alpha + 245 * (1-alpha)).rounded())
            }
            output.pixels[index + 3] = 255
        }
        try save(output, args[3]); return
    }
    guard args.count >= 5 else { throw Failure.invalid("Pose required") }
    let pose = mode == "mask" ? args[4] : args.count == 6 ? args[5] : ""
    let regions = try areas(pose)
    var output = original
    if mode == "mask" {
        output.pixels = [UInt8](repeating: 255, count: original.pixels.count)
        for y in 0..<original.height {
            for x in 0..<original.width {
                let weight = regions.map { $0.weight(x, y) }.max() ?? 0
                output.pixels[(y * original.width + x) * 4 + 3] = UInt8((255 * (1 - weight)).rounded())
            }
        }
        try save(output, args[3])
    } else if mode == "composite", args.count == 6 {
        let edited = try load(args[3])
        guard edited.width == original.width && edited.height == original.height else {
            throw Failure.invalid("Edit dimensions differ from the original")
        }
        var changed = 0
        for y in 0..<original.height {
            for x in 0..<original.width {
                let weight = regions.map { $0.weight(x, y) }.max() ?? 0
                if weight <= 0 { continue }
                let index = (y * original.width + x) * 4
                // Face edits retain original alpha; limb poses can change their silhouette.
                let channels = ["wave", "wave-mid"].contains(pose) ? 4 : 3
                for channel in 0..<channels {
                    let old = Double(original.pixels[index + channel])
                    let next = Double(edited.pixels[index + channel])
                    let value = UInt8(min(255, max(0, (old + (next - old) * weight).rounded())))
                    if value != original.pixels[index + channel] { changed += 1 }
                    output.pixels[index + channel] = value
                }
            }
        }
        try save(output, args[4])
        print("composited_pose=\(pose) changed_channels=\(changed) outside_regions_unchanged=true")
    } else {
        throw Failure.invalid("Unknown operation")
    }
}

do { try run() }
catch { fputs("Mask/composite failed: \(error)\n", stderr); exit(1) }
