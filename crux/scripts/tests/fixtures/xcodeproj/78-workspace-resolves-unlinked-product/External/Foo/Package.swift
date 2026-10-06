// swift-tools-version:5.9
import PackageDescription
let package = Package(
    name: "Foo",
    products: [.library(name: "Foo", targets: ["Foo"])],
    targets: [.target(name: "Foo", dependencies: [])]
)
