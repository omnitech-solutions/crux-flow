// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AmbA",
    products: [
        .library(name: "Bar", targets: ["Bar"]),
    ],
    targets: [
        .target(name: "Bar"),
    ]
)
