// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Plain",
    products: [.library(name: "Pipex", targets: ["Tipey"])],
    targets: [
        .target(name: "Base"),
        .target(name: "Tipey", dependencies: ["Base"]),
    ]
)
