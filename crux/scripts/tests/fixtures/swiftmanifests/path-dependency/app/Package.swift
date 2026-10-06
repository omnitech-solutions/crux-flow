// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "App",
    dependencies: [
        .package(path: "../lib"),
        .package(path: "../../outside"),
        .package(path: "/etc"),
    ],
    targets: [.target(name: "App", dependencies: [.product(name: "Lib", package: "lib")])]
)
