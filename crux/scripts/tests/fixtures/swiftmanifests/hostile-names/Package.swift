// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Hostile",
    products: [.library(name: "P|ipe\r\n```-->x", targets: ["T|ipe\r\n```-->y"])],
    targets: [
        .target(name: "Base"),
        .target(name: "T|ipe\r\n```-->y", dependencies: ["Base"]),
    ]
)
