let package = Package(
    name: "App",
    products: [.library(name: "AppCore", targets: ["AppCore"])],
    dependencies: [
        .package(path: "../../Outside/CorePkg"),
    ],
    targets: [
        .target(name: "AppCore", dependencies: [.product(name: "CoreKit", package: "CorePkg")]),
    ]
)
