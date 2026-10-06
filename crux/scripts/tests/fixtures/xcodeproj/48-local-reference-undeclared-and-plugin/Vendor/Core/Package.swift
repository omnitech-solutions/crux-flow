// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Core",
    products: [
        .library(name: "Other", targets: ["Other"]),
    ],
    targets: [
        .target(name: "Other"),
    ]
)
