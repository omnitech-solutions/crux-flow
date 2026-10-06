// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Kit",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "Kit", targets: ["Kit"]),
        .executable(name: "kit-tool", targets: ["KitTool"]),
        .plugin(name: "KitFormat", targets: ["KitFormat"]),
        .plugin(name: "KitGen", targets: ["KitGen"]),
    ],
    dependencies: [
        .package(url: "https://example.com/a/alpha.git", from: "1.2.0"),
        .package(url: "https://example.com/a/beta.git", exact: "2.0.0"),
        .package(url: "https://example.com/a/gamma.git", branch: "main"),
        .package(url: "https://example.com/a/delta.git", revision: "0123abcd"),
        .package(url: "https://example.com/a/epsilon.git", "1.0.0"..<"2.0.0"),
        .package(path: "Local/Helper"),
    ],
    targets: [
        .target(name: "Kit", dependencies: ["KitCore", .target(name: "KitSupport"), .byName(name: "Helper")]),
        .target(name: "KitCore", path: "Sources/Core"),
        .target(name: "KitSupport", dependencies: [.product(name: "Alpha", package: "alpha")]),
        .executableTarget(name: "KitTool", dependencies: ["Kit"]),
        .testTarget(name: "KitTests", dependencies: ["Kit"]),
        .plugin(name: "KitFormat", capability: .command(intent: .sourceCodeFormatting()), dependencies: ["KitTool"]),
        .plugin(name: "KitGen", capability: .buildTool()),
        .target(name: "Esc\t\"Q\"\\", path: "Sources/Esc"),
        .target(name: "Tr\u{E9}s", path: "Sources/Tres"),
    ]
)
