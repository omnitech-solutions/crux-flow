// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "App",
    dependencies: [
        .package(path: "Pods/PodPkg"),
    ],
    targets: [.target(name: "Core")]
)
