// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Lib",
    products: [.library(name: "Lib", targets: ["Lib"])],
    targets: [.target(name: "Lib")]
)
