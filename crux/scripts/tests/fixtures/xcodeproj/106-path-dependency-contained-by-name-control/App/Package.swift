let package = Package(
    name: "App",
    products: [.library(name: "AppCore", targets: ["AppCore"])],
    dependencies: [
        .package(path: "../Local/CorePkg"),
    ],
    targets: [
        .target(name: "AppCore", dependencies: ["CoreKit"]),
    ]
)
