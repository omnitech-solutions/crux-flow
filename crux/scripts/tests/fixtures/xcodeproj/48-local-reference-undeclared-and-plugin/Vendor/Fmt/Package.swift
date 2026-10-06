// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Fmt",
    products: [
        .plugin(name: "Fmt", targets: ["Fmt"]),
    ],
    targets: [
        .target(name: "Fmt"),
    ]
)
