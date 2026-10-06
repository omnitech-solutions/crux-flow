let package = Package(
    name: "FifoHostile",
    products: [.library(name: "Core", targets: ["Core"])],
    targets: [.target(name: "Core", dependencies: [])]
)
