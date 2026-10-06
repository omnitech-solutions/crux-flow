let package = Package(
    name: "HarnessControl",
    products: [.library(name: "Core", targets: ["Core"])],
    targets: [.target(name: "Core", dependencies: [])]
)
