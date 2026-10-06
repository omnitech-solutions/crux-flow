let package = Package(
    name: "App",
    products: [.library(name: "AppCore", targets: ["AppCore"])],
    dependencies: [
        .package(path: "../Pods/CorePkg"),
    ],
    targets: [
        .target(name: "AppCore", dependencies: ["CoreKit"]),
    ]
)
