// swift-tools-version:5.9
import PackageDescription
let package = Package(
    name: "Pkg",
    products: [.library(name: "Pkg", targets: ["Pkg"])],
    targets: [.target(name: "Pkg", dependencies: [])]
)
