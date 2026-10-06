// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AmbB",
    products: [
        .library(name: "Bar", targets: ["Bar"]),
    ],
    targets: [
        .target(name: "Bar"),
    ]
)
