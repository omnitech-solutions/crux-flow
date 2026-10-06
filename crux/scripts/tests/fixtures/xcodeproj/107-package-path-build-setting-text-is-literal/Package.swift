let package = Package(
    name: "App",
    products: [.library(name: "AppCore", targets: ["AppCore"])],
    dependencies: [
        .package(path: "$(X)/../Local/Core"),
    ],
    targets: [
        .target(name: "AppCore", dependencies: [.product(name: "CoreKit", package: "Core")]),
    ]
)
