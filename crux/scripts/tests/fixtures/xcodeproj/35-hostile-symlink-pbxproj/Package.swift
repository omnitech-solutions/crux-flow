let package = Package(
    name: "SymlinkHostile",
    products: [.library(name: "Core", targets: ["Core"])],
    targets: [.target(name: "Core", dependencies: [])]
)
