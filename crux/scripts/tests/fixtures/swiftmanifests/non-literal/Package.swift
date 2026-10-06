// swift-tools-version:5.9
import PackageDescription

let shared = "Shared"
let package = Package(
    name: "NL",
    products: [.library(name: "\(shared)Lib", targets: ["Core"])],
    targets: [
        .target(name: "Core"),
        .target(name: shared),
        .target(name: "App", dependencies: [
            "Core",
            .product(name: "Remote", package: "remote", condition: .when(platforms: [.linux])),
        ]),
        makeTarget(),
    ]
)

#if os(Linux)
package.targets.append(.target(name: "LinuxOnly", dependencies: ["Core"]))
#endif

package.products.append(.library(name: "Extra", targets: ["Core"]))
