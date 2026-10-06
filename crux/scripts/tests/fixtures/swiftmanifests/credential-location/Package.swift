// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Cred",
    dependencies: [.package(url: "https://u:tok@host/x.git?q=1#f", from: "1.0.0")],
    targets: [.target(name: "Cred", dependencies: [.product(name: "X", package: "x")])]
)
