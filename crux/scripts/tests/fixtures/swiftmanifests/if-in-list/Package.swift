// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Branches",
    products: [
        .library(name: "Base", targets: ["Base"]),
    ],
    targets: [
        .target(name: "Base"),
#if os(Linux)
        .target(name: "Glue", dependencies: ["Base"]),
#else
        .target(name: "Bridge", dependencies: ["Base"]),
#endif
        .target(name: "Front", dependencies: [
            "Base",
#if canImport(Darwin)
            "Bridge",
#endif
        ]),
        .testTarget(name: "FrontTests", dependencies: ["Front"]),
    ]
)
