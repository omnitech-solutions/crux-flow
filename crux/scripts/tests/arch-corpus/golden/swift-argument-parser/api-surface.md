# API surface

_586 interfaces (public, package or open declarations, and every protocol), 48 protocol requirements, 3 package products and 10 `@main` declarations, read through the pinned Swift grammar; nothing was compiled or executed._

## Interfaces

| interface | kind | access | declared at | attributes | conditional |
|---|---|---|---|---|---|
| `ColorOptions` | enum | public | `Examples/color/Color.swift:31-31` | — | — |
| `ColorOptions.defaultValueDescription` | var | public | `Examples/color/Color.swift:36-36` | — | — |
| `ColorOptions.description` | var | public | `Examples/color/Color.swift:47-47` | — | — |
| `GeneratePlugin` | protocol | internal | `Plugins/GenerateCommon/GeneratePlugin.swift:15-15` | — | — |
| `CompletionShell` | struct | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:15-15` | — | — |
| `CompletionShell.rawValue` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:18-18` | — | — |
| `CompletionShell.init(rawValue:)` | init | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:21-21` | — | — |
| `CompletionShell.zsh` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:31-31` | — | — |
| `CompletionShell.bash` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:38-38` | — | — |
| `CompletionShell.fish` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:45-45` | — | — |
| `CompletionShell.autodetected()` | func | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:52-52` | — | — |
| `CompletionShell.allCases` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:57-57` | — | — |
| `CompletionShell.requesting` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:70-70` | — | — |
| `CompletionShell.requestingVersion` | var | public | `Sources/ArgumentParser/Completions/CompletionsGenerator.swift:82-82` | — | — |
| `Argument` | struct | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:44-45` | @propertyWrapper | — |
| `Argument.init(from:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:54-54` | — | — |
| `Argument.init()` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:69-74` | @available | — |
| `Argument.wrappedValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:79-79` | — | — |
| `Argument.description` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:95-95` | — | — |
| `ArgumentArrayParsingStrategy` | struct | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:110-110` | — | — |
| `ArgumentArrayParsingStrategy.remaining` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:156-156` | — | — |
| `ArgumentArrayParsingStrategy.allUnrecognized` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:193-193` | — | — |
| `ArgumentArrayParsingStrategy.postTerminator` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:241-241` | — | — |
| `ArgumentArrayParsingStrategy.captureForPassthrough` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:303-303` | — | — |
| `ArgumentArrayParsingStrategy.unconditionalRemaining` | var | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:307-308` | @available | — |
| `Argument.init(wrappedValue:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:331-331` | — | — |
| `Argument.init(help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:362-362` | — | — |
| `Argument.init(wrappedValue:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:401-402` | @preconcurrency | — |
| `Argument.init(help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:437-439` | @preconcurrency, @_disfavoredOverload | — |
| `Argument.init(wrappedValue:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:472-472` | — | — |
| `Argument.init(wrappedValue:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:492-499` | @available, @_disfavoredOverload | — |
| `Argument.init(help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:527-527` | — | — |
| `Argument.init(wrappedValue:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:560-561` | @preconcurrency | — |
| `Argument.init(wrappedValue:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:583-591` | @available, @_disfavoredOverload, @preconcurrency | — |
| `Argument.init(help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:623-624` | @preconcurrency | — |
| `Argument.init(wrappedValue:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:656-656` | — | — |
| `Argument.init(parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:692-692` | — | — |
| `Argument.init(wrappedValue:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:726-727` | @preconcurrency | — |
| `Argument.init(parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Argument.swift:767-768` | @preconcurrency | — |
| `ArgumentHelp` | struct | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:13-13` | — | — |
| `ArgumentHelp.abstract` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:15-15` | — | — |
| `ArgumentHelp.discussion` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:18-18` | — | — |
| `ArgumentHelp.valueName` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:25-25` | — | — |
| `ArgumentHelp.visibility` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:29-29` | — | — |
| `ArgumentHelp.shouldDisplay` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:33-34` | @available | — |
| `ArgumentHelp.argumentType` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:45-45` | — | — |
| `ArgumentHelp.init(_:discussion:valueName:shouldDisplay:)` | init | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:48-52` | @available | — |
| `ArgumentHelp.init(_:discussion:valueName:visibility:argumentType:)` | init | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:65-65` | — | — |
| `ArgumentHelp.hidden` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:80-80` | — | — |
| `ArgumentHelp.ʼprivateʼ` | var | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:85-85` | — | — |
| `ArgumentHelp.init(stringLiteral:)` | init | public | `Sources/ArgumentParser/Parsable Properties/ArgumentHelp.swift:93-93` | — | — |
| `ArgumentVisibility` | struct | public | `Sources/ArgumentParser/Parsable Properties/ArgumentVisibility.swift:13-13` | — | — |
| `ArgumentVisibility.ʼdefaultʼ` | let | public | `Sources/ArgumentParser/Parsable Properties/ArgumentVisibility.swift:25-25` | — | — |
| `ArgumentVisibility.hidden` | let | public | `Sources/ArgumentParser/Parsable Properties/ArgumentVisibility.swift:28-28` | — | — |
| `ArgumentVisibility.ʼprivateʼ` | let | public | `Sources/ArgumentParser/Parsable Properties/ArgumentVisibility.swift:31-31` | — | — |
| `CompletionKind` | struct | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:36-36` | — | — |
| `CompletionKind.ʼdefaultʼ` | var | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:51-51` | — | — |
| `CompletionKind.list(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:63-63` | — | — |
| `CompletionKind.file(extensions:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:81-81` | — | — |
| `CompletionKind.directory` | var | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:89-89` | — | — |
| `CompletionKind.shellCommand(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:101-101` | — | — |
| `CompletionKind.custom(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:173-174` | @preconcurrency | — |
| `CompletionKind.custom(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:184-185` | @available | — |
| `CompletionKind.custom(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/CompletionKind.swift:195-202` | @preconcurrency, @available | — |
| `ValidationError` | struct | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:14-14` | — | — |
| `ValidationError.message` | var | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:18-18` | — | — |
| `ValidationError.init(_:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:21-21` | — | — |
| `ValidationError.description` | var | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:25-25` | — | — |
| `ExitCode` | struct | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:35-35` | — | — |
| `ExitCode.rawValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:37-37` | — | — |
| `ExitCode.init(_:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:40-40` | — | — |
| `ExitCode.init(rawValue:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:44-44` | — | — |
| `ExitCode.success` | let | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:49-49` | — | — |
| `ExitCode.failure` | let | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:52-52` | — | — |
| `ExitCode.validationFailure` | let | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:55-55` | — | — |
| `ExitCode.isSuccess` | var | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:60-60` | — | — |
| `CleanExit` | struct | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:70-70` | — | — |
| `CleanExit.helpRequest(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:88-88` | — | — |
| `CleanExit.message(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:95-95` | — | — |
| `CleanExit.helpRequest(_:)` | func | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:108-108` | — | — |
| `CleanExit.description` | var | public | `Sources/ArgumentParser/Parsable Properties/Errors.swift:112-112` | — | — |
| `Flag` | struct | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:71-72` | @propertyWrapper | — |
| `Flag.init(from:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:79-79` | — | — |
| `Flag.init()` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:94-99` | @available | — |
| `Flag.wrappedValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:104-104` | — | — |
| `Flag.description` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:122-122` | — | — |
| `FlagInversion` | struct | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:135-135` | — | — |
| `FlagInversion.prefixedNo` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:151-151` | — | — |
| `FlagInversion.prefixedEnableDisable` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:164-164` | — | — |
| `FlagExclusivity` | struct | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:172-172` | — | — |
| `FlagExclusivity.exclusive` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:182-182` | — | — |
| `FlagExclusivity.chooseFirst` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:187-187` | — | — |
| `FlagExclusivity.chooseLast` | var | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:192-192` | — | — |
| `Flag.init(name:inversion:exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:219-219` | — | — |
| `Flag.init(wrappedValue:name:inversion:exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:241-241` | — | — |
| `Flag.init(name:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:261-266` | @available | — |
| `Flag.init(wrappedValue:name:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:295-295` | — | — |
| `Flag.init(wrappedValue:name:inversion:exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:347-347` | — | — |
| `Flag.init(name:inversion:exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:378-378` | — | — |
| `Flag.init(name:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:403-403` | — | — |
| `Flag.init(wrappedValue:exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:500-500` | — | — |
| `Flag.init(exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:530-530` | — | — |
| `Flag.init(exclusivity:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:545-545` | — | — |
| `Flag.init(wrappedValue:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:633-633` | — | — |
| `Flag.init(help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Flag.swift:652-652` | — | — |
| `NameSpecification` | struct | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:33-33` | — | — |
| `NameSpecification.Element` | struct | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:35-35` | — | — |
| `NameSpecification.Element.long` | var | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:51-51` | — | — |
| `NameSpecification.Element.customLong(_:withSingleDash:)` | func | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:67-67` | — | — |
| `NameSpecification.Element.short` | var | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:78-78` | — | — |
| `NameSpecification.Element.customShort(_:allowingJoined:)` | func | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:95-95` | — | — |
| `NameSpecification.init(_:)` | init | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:113-113` | — | — |
| `NameSpecification.init(arrayLiteral:)` | init | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:117-117` | — | — |
| `NameSpecification.Element.init(stringLiteral:)` | init | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:127-127` | — | — |
| `NameSpecification.init(stringLiteral:)` | init | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:172-172` | — | — |
| `NameSpecification.long` | var | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:195-195` | — | — |
| `NameSpecification.customLong(_:withSingleDash:)` | func | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:209-209` | — | — |
| `NameSpecification.short` | var | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:220-220` | — | — |
| `NameSpecification.customShort(_:allowingJoined:)` | func | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:235-235` | — | — |
| `NameSpecification.shortAndLong` | var | public | `Sources/ArgumentParser/Parsable Properties/NameSpecification.swift:247-247` | — | — |
| `Option` | struct | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:51-52` | @propertyWrapper | — |
| `Option.init(from:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:59-59` | — | — |
| `Option.init()` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:74-79` | @available | — |
| `Option.wrappedValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:84-84` | — | — |
| `Option.description` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:100-100` | — | — |
| `SingleValueParsingStrategy` | struct | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:116-116` | — | — |
| `SingleValueParsingStrategy.next` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:130-130` | — | — |
| `SingleValueParsingStrategy.unconditional` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:146-146` | — | — |
| `SingleValueParsingStrategy.scanningForValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:159-159` | — | — |
| `DefaultAsFlagParsingStrategy` | struct | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:168-168` | — | — |
| `DefaultAsFlagParsingStrategy.next` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:182-182` | — | — |
| `DefaultAsFlagParsingStrategy.scanningForValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:197-197` | — | — |
| `ArrayParsingStrategy` | struct | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:207-207` | — | — |
| `ArrayParsingStrategy.singleValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:222-222` | — | — |
| `ArrayParsingStrategy.unconditionalSingleValue` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:239-239` | — | — |
| `ArrayParsingStrategy.upToNextOption` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:253-253` | — | — |
| `ArrayParsingStrategy.remaining` | var | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:280-280` | — | — |
| `Option.init(wrappedValue:name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:308-308` | — | — |
| `Option.init(wrappedValue:name:parsing:completion:help:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:336-342` | @available | — |
| `Option.init(name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:373-373` | — | — |
| `Option.init(wrappedValue:name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:425-426` | @preconcurrency | — |
| `Option.init(name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:470-472` | @preconcurrency, @_disfavoredOverload | — |
| `Option.init(wrappedValue:name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:517-517` | — | — |
| `Option.init(wrappedValue:name:defaultAsFlag:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:564-564` | — | — |
| `Option.init(wrappedValue:name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:609-616` | @available, @_disfavoredOverload | — |
| `Option.init(name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:660-660` | — | — |
| `Option.init(name:defaultAsFlag:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:704-704` | — | — |
| `Option.init(wrappedValue:name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:774-775` | @preconcurrency | — |
| `Option.init(wrappedValue:name:defaultAsFlag:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:821-822` | @preconcurrency | — |
| `Option.init(wrappedValue:name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:871-879` | @available, @_disfavoredOverload, @preconcurrency | — |
| `Option.init(name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:923-924` | @preconcurrency | — |
| `Option.init(name:defaultAsFlag:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:967-968` | @preconcurrency | — |
| `Option.init(wrappedValue:name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:1046-1046` | — | — |
| `Option.init(name:parsing:help:completion:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:1097-1097` | — | — |
| `Option.init(wrappedValue:name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:1151-1152` | @preconcurrency | — |
| `Option.init(name:parsing:help:completion:transform:)` | init | public | `Sources/ArgumentParser/Parsable Properties/Option.swift:1196-1197` | @preconcurrency | — |
| `OptionGroup` | struct | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:33-34` | @propertyWrapper | — |
| `OptionGroup.title` | var | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:43-43` | — | — |
| `OptionGroup.init(from:)` | init | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:50-50` | — | — |
| `OptionGroup.init(title:visibility:)` | init | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:78-78` | — | — |
| `OptionGroup.wrappedValue` | var | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:101-101` | — | — |
| `OptionGroup.description` | var | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:119-119` | — | — |
| `OptionGroup.init(_hiddenFromHelp:)` | init | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:131-132` | @available | — |
| `OptionGroup.init()` | init | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:137-139` | @available, @_disfavoredOverload | — |
| `OptionGroup.init(visibility:)` | init | public | `Sources/ArgumentParser/Parsable Properties/OptionGroup.swift:147-149` | @_disfavoredOverload, @available | — |
| `ParentCommand` | struct | public | `Sources/ArgumentParser/Parsable Properties/ParentCommand.swift:42-43` | @propertyWrapper | — |
| `ParentCommand.init(from:)` | init | public | `Sources/ArgumentParser/Parsable Properties/ParentCommand.swift:50-50` | — | — |
| `ParentCommand.init()` | init | public | `Sources/ArgumentParser/Parsable Properties/ParentCommand.swift:60-60` | — | — |
| `ParentCommand.wrappedValue` | var | public | `Sources/ArgumentParser/Parsable Properties/ParentCommand.swift:68-68` | — | — |
| `ParentCommand.description` | var | public | `Sources/ArgumentParser/Parsable Properties/ParentCommand.swift:86-86` | — | — |
| `AsyncParsableCommand` | protocol | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:14-15` | @available | — |
| `AsyncParsableCommand.asyncParse(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:34-34` | — | — |
| `AsyncParsableCommand.asyncParseAsRoot(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:48-48` | — | — |
| `AsyncParsableCommand.main(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:65-65` | — | — |
| `AsyncParsableCommand.main()` | func | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:88-88` | — | — |
| `AsyncMainProtocol` | protocol | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:97-101` | @available | — |
| `AsyncMainProtocol.main()` | func | public | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:110-110` | — | — |
| `CommandConfiguration` | struct | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:13-13` | — | — |
| `CommandConfiguration.commandName` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:18-18` | — | — |
| `CommandConfiguration._superCommandName` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:25-25` | — | — |
| `CommandConfiguration.abstract` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:28-28` | — | — |
| `CommandConfiguration.usage` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:36-36` | — | — |
| `CommandConfiguration.discussion` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:44-44` | — | — |
| `CommandConfiguration.helpBanner` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:53-53` | — | — |
| `CommandConfiguration.version` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:56-56` | — | — |
| `CommandConfiguration.shouldDisplay` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:60-60` | — | — |
| `CommandConfiguration.subcommands` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:66-66` | — | — |
| `CommandConfiguration.ungroupedSubcommands` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:79-79` | — | — |
| `CommandConfiguration.groupedSubcommands` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:82-82` | — | — |
| `CommandConfiguration.defaultSubcommand` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:85-85` | — | — |
| `CommandConfiguration.helpNames` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:88-88` | — | — |
| `CommandConfiguration.aliases` | var | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:95-95` | — | — |
| `CommandConfiguration.init(commandName:abstract:usage:discussion:helpBanner:version:shouldDisplay:subcommands:groupedSubcommands:defaultSubcommand:helpNames:aliases:)` | init | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:132-132` | — | — |
| `CommandConfiguration.init(commandName:_superCommandName:abstract:usage:discussion:helpBanner:version:shouldDisplay:subcommands:groupedSubcommands:defaultSubcommand:helpNames:aliases:)` | init | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:162-162` | — | — |
| `CommandConfiguration.init(commandName:abstract:usage:discussion:version:shouldDisplay:subcommands:defaultSubcommand:helpNames:)` | init | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:194-198` | @available | — |
| `CommandConfiguration.init(commandName:abstract:discussion:version:shouldDisplay:subcommands:defaultSubcommand:helpNames:)` | init | public | `Sources/ArgumentParser/Parsable Types/CommandConfiguration.swift:222-227` | @available | — |
| `CommandGroup` | struct | public | `Sources/ArgumentParser/Parsable Types/CommandGroup.swift:13-13` | — | — |
| `CommandGroup.name` | let | public | `Sources/ArgumentParser/Parsable Types/CommandGroup.swift:15-15` | — | — |
| `CommandGroup.subcommands` | let | public | `Sources/ArgumentParser/Parsable Types/CommandGroup.swift:18-18` | — | — |
| `CommandGroup.init(name:subcommands:)` | init | public | `Sources/ArgumentParser/Parsable Types/CommandGroup.swift:21-21` | — | — |
| `EnumerableFlag` | protocol | public | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:58-58` | — | — |
| `EnumerableFlag.name(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:76-76` | — | — |
| `EnumerableFlag.help(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:80-80` | — | — |
| `EnumerableFlag.description` | var | public | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:87-87` | — | — |
| `ExpressibleByArgument` | protocol | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:13-13` | — | — |
| `ExpressibleByArgument.defaultValueDescription` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:49-49` | — | — |
| `ExpressibleByArgument.allValueStrings` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:53-53` | — | — |
| `ExpressibleByArgument.allValueDescriptions` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:55-55` | — | — |
| `ExpressibleByArgument.defaultCompletionKind` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:57-57` | — | — |
| `ExpressibleByArgument.allValueStrings` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:63-63` | — | — |
| `ExpressibleByArgument.defaultCompletionKind` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:67-67` | — | — |
| `ExpressibleByArgument.allValueStrings` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:76-76` | — | — |
| `ExpressibleByArgument.allValueDescriptions` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:80-80` | — | — |
| `ExpressibleByArgument.defaultValueDescription` | var | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:95-95` | — | — |
| `String.init(argument:)` | init | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:101-101` | — | — |
| `RawRepresentable.init(argument:)` | init | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:108-108` | — | — |
| `LosslessStringConvertible.init(argument:)` | init | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:120-120` | — | — |
| `LosslessStringConvertible.init(argument:)` | init | public | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:131-131` | — | — |
| `ParsableArguments` | protocol | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:16-16` | — | — |
| `ParsableArguments.validate()` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:60-60` | — | — |
| `ParsableArguments._errorLabel` | var | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:68-68` | — | — |
| `ParsableArguments._errorPrefix` | var | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:75-75` | — | — |
| `ParsableArguments.parse(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:89-89` | — | — |
| `ParsableArguments.message(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:116-116` | — | — |
| `ParsableArguments.fullMessage(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:122-124` | @available, @_disfavoredOverload | — |
| `ParsableArguments.fullMessage(for:columns:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:140-140` | — | — |
| `ParsableArguments.helpMessage(columns:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:153-157` | @_disfavoredOverload, @available | — |
| `ParsableArguments.helpMessage(includeHidden:columns:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:173-173` | — | — |
| `ParsableArguments._dumpHelp()` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:182-182` | — | — |
| `ParsableArguments.exitCode(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:193-193` | — | — |
| `ParsableArguments.completionScript(for:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:203-203` | — | — |
| `ParsableArguments.exit(withError:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:223-223` | — | — |
| `ParsableArguments.parseOrExit(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:250-250` | — | — |
| `ParsableArguments.usageString(includeHidden:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:265-265` | — | — |
| `ArgumentSetProvider` | protocol | internal | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:291-291` | — | — |
| `ParsableCommand` | protocol | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:13-13` | — | — |
| `ParsableCommand._commandName` | var | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:38-38` | — | — |
| `ParsableCommand.configuration` | var | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:43-43` | — | — |
| `ParsableCommand.run()` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:47-47` | — | — |
| `ParsableCommand.parseAsRoot(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:63-63` | — | — |
| `ParsableCommand.helpMessage(for:columns:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:83-85` | @_disfavoredOverload, @available | — |
| `ParsableCommand.helpMessage(for:includeHidden:columns:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:106-106` | — | — |
| `ParsableCommand.usageString(for:includeHidden:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:127-127` | — | — |
| `ParsableCommand.main(_:)` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:148-148` | — | — |
| `ParsableCommand.main()` | func | public | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:177-177` | — | — |
| `ArrayWrapperProtocol` | protocol | internal | `Sources/ArgumentParser/Parsing/ArgumentDecoder.swift:297-297` | — | — |
| `ArgumentDefinitionContainer` | protocol | internal | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:363-363` | — | — |
| `ArgumentDefinitionContainerExpressibleByArgument` | protocol | internal | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:375-375` | — | — |
| `ParsedWrapper` | protocol | internal | `Sources/ArgumentParser/Parsing/Parsed.swift:38-38` | — | — |
| `DecodableParsedWrapper` | protocol | internal | `Sources/ArgumentParser/Parsing/Parsed.swift:48-48` | — | — |
| `_SendableMetatype` | protocol | public | `Sources/ArgumentParser/Utilities/SwiftExtensions.swift:14-14` | @_marker | yes |
| `_SendableMetatype` | protocol | public | `Sources/ArgumentParser/Utilities/SwiftExtensions.swift:16-16` | @_marker | yes |
| `AnyOptionGroup` | protocol | private | `Sources/ArgumentParser/Validators/AsyncCompletionsValidator.swift:55-55` | — | — |
| `ParsableArgumentsValidator` | protocol | internal | `Sources/ArgumentParser/Validators/ParsableArgumentsValidation.swift:39-39` | — | — |
| `ParsableArgumentsValidatorError` | protocol | internal | `Sources/ArgumentParser/Validators/ParsableArgumentsValidation.swift:49-49` | — | — |
| `Trait.requiresProcessExecution` | var | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting+Tags.swift:25-25` | — | — |
| `TestExpectation` | class | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:26-26` | — | — |
| `TestExpectation.fulfilled` | var | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:27-27` | — | — |
| `TestExpectation.init()` | init | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:29-29` | — | — |
| `TestExpectation.fulfill()` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:31-31` | — | — |
| `TestableSwiftTestingParsableArguments` | protocol | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:36-36` | — | — |
| `TestableSwiftTestingParsableArguments.validate()` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:41-41` | — | — |
| `TestableSwiftTestingParsableCommand` | protocol | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:46-46` | — | — |
| `TestableSwiftTestingParsableCommand.run()` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:53-53` | — | — |
| `CollectionDifference.Change.<(lhs:rhs:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:31-31` | — | — |
| `TestableParsableArguments` | protocol | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:47-52` | @available | — |
| `TestableParsableArguments.validate()` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:57-58` | @available | — |
| `TestableParsableCommand` | protocol | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:64-69` | @available | — |
| `TestableParsableCommand.run()` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:76-77` | @available | — |
| `XCTestExpectation.init(singleExpectation:)` | init | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:83-88` | @available | — |
| `AssertResultFailure(_:_:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:96-100` | @available | — |
| `AssertErrorMessage(_:_:_:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:117-121` | @available | — |
| `AssertFullErrorMessage(_:_:_:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:138-142` | @available | — |
| `AssertParse(_:_:file:line:closure:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:158-162` | @available | — |
| `AssertParseCommand(_:_:_:file:line:closure:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:177-181` | @available | — |
| `AssertParseCommandErrorMessage(_:_:_:_:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:202-207` | @available | — |
| `AssertEqualStrings(actual:expected:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:228-232` | @available | — |
| `XCTest.debugURL` | var | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:313-318` | @available | — |
| `XCTest.AssertExecuteCommand(command:expected:exitCode:file:line:environment:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:326-331` | @available, @discardableResult | — |
| `XCTest.AssertExecuteCommand(command:expected:exitCode:file:line:environment:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:350-355` | @available, @discardableResult | — |
| `XCTest.AssertJSONEqualFromString(actual:expected:for:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:455-460` | @available | — |
| `XCTest.assertSnapshot(actual:extension:record:test:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:557-562` | @available, @discardableResult | — |
| `XCTest.assertGenerateManual(multiPage:command:record:test:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:621-625` | @available | — |
| `XCTest.assertGeneratedReference(command:doccFlavored:record:test:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:664-669` | @available | — |
| `XCTest.assertDumpHelp(type:record:test:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:709-713` | @available | — |
| `XCTest.assertDumpHelp(command:record:test:file:line:)` | func | public | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:762-767` | @available | — |
| `ToolInfoHeader` | struct | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:18-18` | — | — |
| `ToolInfoHeader.serializationVersion` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:21-21` | — | — |
| `ToolInfoHeader.init(serializationVersion:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:23-23` | — | — |
| `ToolInfoV0` | struct | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:30-30` | — | — |
| `ToolInfoV0.serializationVersion` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:33-33` | — | — |
| `ToolInfoV0.command` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:35-35` | — | — |
| `ToolInfoV0.init(command:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:37-37` | — | — |
| `CommandInfoV0` | struct | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:44-44` | — | — |
| `CommandInfoV0.superCommands` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:46-46` | — | — |
| `CommandInfoV0.shouldDisplay` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:48-48` | — | — |
| `CommandInfoV0.commandName` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:51-51` | — | — |
| `CommandInfoV0.aliases` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:53-53` | — | — |
| `CommandInfoV0.abstract` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:55-55` | — | — |
| `CommandInfoV0.discussion` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:57-57` | — | — |
| `CommandInfoV0.defaultSubcommand` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:61-61` | — | — |
| `CommandInfoV0.subcommands` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:63-63` | — | — |
| `CommandInfoV0.arguments` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:65-65` | — | — |
| `CommandInfoV0.init(superCommands:shouldDisplay:commandName:aliases:abstract:discussion:defaultSubcommand:subcommands:arguments:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:67-67` | — | — |
| `CommandInfoV0.init(from:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:91-91` | — | — |
| `ArgumentInfoV0` | struct | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:115-115` | — | — |
| `ArgumentInfoV0.NameInfoV0` | struct | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:117-117` | — | — |
| `ArgumentInfoV0.NameInfoV0.KindV0` | enum | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:119-119` | — | — |
| `ArgumentInfoV0.NameInfoV0.kind` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:129-129` | — | — |
| `ArgumentInfoV0.NameInfoV0.name` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:131-131` | — | — |
| `ArgumentInfoV0.NameInfoV0.init(kind:name:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:133-133` | — | — |
| `ArgumentInfoV0.KindV0` | enum | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:140-140` | — | — |
| `ArgumentInfoV0.ParsingStrategyV0` | enum | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:149-149` | — | — |
| `ArgumentInfoV0.CompletionKindV0` | enum | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:169-169` | — | — |
| `ArgumentInfoV0.kind` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:188-188` | — | — |
| `ArgumentInfoV0.shouldDisplay` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:191-191` | — | — |
| `ArgumentInfoV0.sectionTitle` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:193-193` | — | — |
| `ArgumentInfoV0.isOptional` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:196-196` | — | — |
| `ArgumentInfoV0.isRepeating` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:198-198` | — | — |
| `ArgumentInfoV0.parsingStrategy` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:201-201` | — | — |
| `ArgumentInfoV0.names` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:204-204` | — | — |
| `ArgumentInfoV0.preferredName` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:206-206` | — | — |
| `ArgumentInfoV0.valueName` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:209-209` | — | — |
| `ArgumentInfoV0.defaultValue` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:211-211` | — | — |
| `ArgumentInfoV0.allValues` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:217-217` | — | — |
| `ArgumentInfoV0.allValueStrings` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:219-219` | — | — |
| `ArgumentInfoV0.allValueDescriptions` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:224-224` | — | — |
| `ArgumentInfoV0.completionKind` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:229-229` | — | — |
| `ArgumentInfoV0.abstract` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:232-232` | — | — |
| `ArgumentInfoV0.discussion` | var | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:234-234` | — | — |
| `ArgumentInfoV0.init(kind:shouldDisplay:sectionTitle:isOptional:isRepeating:parsingStrategy:names:preferredName:valueName:defaultValue:allValueStrings:allValueDescriptions:completionKind:abstract:discussion:)` | init | public | `Sources/ArgumentParserToolInfo/ToolInfo.swift:236-236` | — | — |
| `Range<Int>.init(argument:)` | init | public | `Tests/ArgumentParserEndToEndTests/PositionalEndToEndTests.swift:260-260` | — | — |
| `Convert` | protocol | private | `Tests/ArgumentParserEndToEndTests/TransformEndToEndTests.swift:22-22` | — | — |
| `Package.Config.configuration` | let | public | `Tests/ArgumentParserPackageManagerTests/PackageManager/Config.swift:20-20` | — | — |
| `DecodingError.description` | var | public | `Tests/ArgumentParserToolInfoTests/ArgumentParserToolInfoTests.swift:17-17` | — | — |
| `Foundation.URL.init(argument:)` | init | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:24-24` | — | — |
| `Foundation.URL.defaultValueDescription` | var | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:31-31` | — | — |
| `HelpGenerationTests.Foo.configuration` | let | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:531-531` | — | — |
| `HelpGenerationTests.Foo.init()` | init | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:542-542` | — | — |
| `HelpGenerationTests.Bar.init()` | init | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:555-555` | — | — |
| `HelpGenerationTests.OptionValues.defaultValueDescription` | var | public | `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift:1070-1070` | — | — |
| `SendableTests.MyExpressibleType.init(argument:)` | init | public | `Tests/ArgumentParserUnitTests/SendableTests.swift:19-19` | — | — |
| `ArgumentParser.SplitArguments.InputIndex.init(integerLiteral:)` | init | public | `Tests/ArgumentParserUnitTests/SplitArgumentTests.swift:20-20` | — | — |
| `CommandInfoV0.usage(startlength:wraplength:)` | func | public | `Tools/generate-docc-reference/Extensions/ArgumentParser+Markdown.swift:117-117` | — | — |
| `ArgumentInfoV0.usage()` | func | public | `Tools/generate-docc-reference/Extensions/ArgumentParser+Markdown.swift:147-147` | — | — |
| `ArgumentInfoV0.identity()` | func | public | `Tools/generate-docc-reference/Extensions/ArgumentParser+Markdown.swift:187-187` | — | — |
| `AuthorArgument.init(argument:)` | init | public | `Tools/generate-manual/AuthorArgument.swift:46-46` | — | — |
| `MDocComponent` | protocol | internal | `Tools/generate-manual/DSL/Core/MDocComponent.swift:12-12` | — | — |
| `Foundation.Date.init(argument:)` | init | public | `Tools/generate-manual/Extensions/Date+ExpressibleByArgument.swift:17-17` | — | — |
| `MDocASTNode` | protocol | public | `Tools/generate-manual/MDoc/MDocASTNode.swift:18-18` | — | — |
| `MDocASTNode.serialized()` | func | public | `Tools/generate-manual/MDoc/MDocASTNode.swift:27-27` | — | — |
| `Int._serialized(context:)` | func | public | `Tools/generate-manual/MDoc/MDocASTNode.swift:33-33` | — | — |
| `String._serialized(context:)` | func | public | `Tools/generate-manual/MDoc/MDocASTNode.swift:39-39` | — | — |
| `MDocMacroProtocol` | protocol | public | `Tools/generate-manual/MDoc/MDocMacro.swift:88-88` | — | — |
| `MDocMacroProtocol.withUnsafeChildren(nodes:)` | func | public | `Tools/generate-manual/MDoc/MDocMacro.swift:98-98` | — | — |
| `MDocMacroProtocol._serialized(context:)` | func | public | `Tools/generate-manual/MDoc/MDocMacro.swift:106-106` | — | — |
| `MDocMacro` | enum | public | `Tools/generate-manual/MDoc/MDocMacro.swift:130-130` | — | — |
| `MDocMacro.Comment` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:139-139` | — | — |
| `MDocMacro.Comment.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:140-140` | — | — |
| `MDocMacro.Comment.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:141-141` | — | — |
| `MDocMacro.Comment.init(_:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:145-145` | — | — |
| `MDocMacro.DocumentDate` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:160-160` | — | — |
| `MDocMacro.DocumentDate.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:161-161` | — | — |
| `MDocMacro.DocumentDate.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:162-162` | — | — |
| `MDocMacro.DocumentDate.init(day:month:year:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:169-169` | — | — |
| `MDocMacro.DocumentTitle` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:183-183` | — | — |
| `MDocMacro.DocumentTitle.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:184-184` | — | — |
| `MDocMacro.DocumentTitle.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:185-185` | — | — |
| `MDocMacro.DocumentTitle.init(title:section:arch:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:204-204` | — | — |
| `MDocMacro.OperatingSystem` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:221-221` | — | — |
| `MDocMacro.OperatingSystem.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:222-222` | — | — |
| `MDocMacro.OperatingSystem.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:223-223` | — | — |
| `MDocMacro.OperatingSystem.init(name:version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:236-236` | — | — |
| `MDocMacro.DocumentName` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:262-262` | — | — |
| `MDocMacro.DocumentName.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:263-263` | — | — |
| `MDocMacro.DocumentName.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:264-264` | — | — |
| `MDocMacro.DocumentName.init(name:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:268-268` | — | — |
| `MDocMacro.DocumentDescription` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:283-283` | — | — |
| `MDocMacro.DocumentDescription.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:284-284` | — | — |
| `MDocMacro.DocumentDescription.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:285-285` | — | — |
| `MDocMacro.DocumentDescription.init(description:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:289-289` | — | — |
| `MDocMacro.SectionHeader` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:309-309` | — | — |
| `MDocMacro.SectionHeader.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:310-310` | — | — |
| `MDocMacro.SectionHeader.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:311-311` | — | — |
| `MDocMacro.SectionHeader.init(title:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:315-315` | — | — |
| `MDocMacro.SubsectionHeader` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:331-331` | — | — |
| `MDocMacro.SubsectionHeader.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:332-332` | — | — |
| `MDocMacro.SubsectionHeader.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:333-333` | — | — |
| `MDocMacro.SubsectionHeader.init(title:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:337-337` | — | — |
| `MDocMacro.SectionReference` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:351-351` | — | — |
| `MDocMacro.SectionReference.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:352-352` | — | — |
| `MDocMacro.SectionReference.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:353-353` | — | — |
| `MDocMacro.SectionReference.init(title:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:357-357` | — | — |
| `MDocMacro.CrossManualReference` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:368-368` | — | — |
| `MDocMacro.CrossManualReference.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:369-369` | — | — |
| `MDocMacro.CrossManualReference.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:370-370` | — | — |
| `MDocMacro.CrossManualReference.init(title:section:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:376-376` | — | — |
| `MDocMacro.ParagraphBreak` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:386-386` | — | — |
| `MDocMacro.ParagraphBreak.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:387-387` | — | — |
| `MDocMacro.ParagraphBreak.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:388-388` | — | — |
| `MDocMacro.ParagraphBreak.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:390-390` | — | — |
| `MDocMacro.BeginList` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:428-428` | — | — |
| `MDocMacro.BeginList.ListStyle` | enum | public | `Tools/generate-manual/MDoc/MDocMacro.swift:430-430` | — | — |
| `MDocMacro.BeginList.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:484-484` | — | — |
| `MDocMacro.BeginList.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:485-485` | — | — |
| `MDocMacro.BeginList.init(style:width:offset:compact:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:493-493` | — | — |
| `MDocMacro.ListItem` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:536-536` | — | — |
| `MDocMacro.ListItem.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:537-537` | — | — |
| `MDocMacro.ListItem.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:538-538` | — | — |
| `MDocMacro.ListItem.init(title:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:543-543` | — | — |
| `MDocMacro.EndList` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:553-553` | — | — |
| `MDocMacro.EndList.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:554-554` | — | — |
| `MDocMacro.EndList.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:555-555` | — | — |
| `MDocMacro.EndList.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:557-557` | — | — |
| `MDocMacro.WithoutTrailingSpace` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:573-573` | — | — |
| `MDocMacro.WithoutTrailingSpace.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:574-574` | — | — |
| `MDocMacro.WithoutTrailingSpace.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:575-575` | — | — |
| `MDocMacro.WithoutTrailingSpace.init(text:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:579-579` | — | — |
| `MDocMacro.WithoutLeadingSpace` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:590-590` | — | — |
| `MDocMacro.WithoutLeadingSpace.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:591-591` | — | — |
| `MDocMacro.WithoutLeadingSpace.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:592-592` | — | — |
| `MDocMacro.WithoutLeadingSpace.init(text:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:596-596` | — | — |
| `MDocMacro.Apostrophe` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:607-607` | — | — |
| `MDocMacro.Apostrophe.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:608-608` | — | — |
| `MDocMacro.Apostrophe.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:609-609` | — | — |
| `MDocMacro.Apostrophe.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:611-611` | — | — |
| `MDocMacro.CommandOption` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:642-642` | — | — |
| `MDocMacro.CommandOption.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:643-643` | — | — |
| `MDocMacro.CommandOption.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:644-644` | — | — |
| `MDocMacro.CommandOption.init(options:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:648-648` | — | — |
| `MDocMacro.CommandModifier` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:665-665` | — | — |
| `MDocMacro.CommandModifier.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:666-666` | — | — |
| `MDocMacro.CommandModifier.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:667-667` | — | — |
| `MDocMacro.CommandModifier.init(modifiers:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:671-671` | — | — |
| `MDocMacro.CommandArgument` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:690-690` | — | — |
| `MDocMacro.CommandArgument.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:691-691` | — | — |
| `MDocMacro.CommandArgument.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:692-692` | — | — |
| `MDocMacro.CommandArgument.init(arguments:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:696-696` | — | — |
| `MDocMacro.OptionalCommandLineComponent` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:713-713` | — | — |
| `MDocMacro.OptionalCommandLineComponent.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:714-714` | — | — |
| `MDocMacro.OptionalCommandLineComponent.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:715-715` | — | — |
| `MDocMacro.OptionalCommandLineComponent.init(arguments:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:719-719` | — | — |
| `MDocMacro.BeginOptionalCommandLineComponent` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:738-738` | — | — |
| `MDocMacro.BeginOptionalCommandLineComponent.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:739-739` | — | — |
| `MDocMacro.BeginOptionalCommandLineComponent.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:740-740` | — | — |
| `MDocMacro.BeginOptionalCommandLineComponent.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:742-742` | — | — |
| `MDocMacro.EndOptionalCommandLineComponent` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:748-748` | — | — |
| `MDocMacro.EndOptionalCommandLineComponent.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:749-749` | — | — |
| `MDocMacro.EndOptionalCommandLineComponent.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:750-750` | — | — |
| `MDocMacro.EndOptionalCommandLineComponent.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:752-752` | — | — |
| `MDocMacro.InteractiveCommand` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:770-770` | — | — |
| `MDocMacro.InteractiveCommand.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:771-771` | — | — |
| `MDocMacro.InteractiveCommand.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:772-772` | — | — |
| `MDocMacro.InteractiveCommand.init(name:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:776-776` | — | — |
| `MDocMacro.EnvironmentVariable` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:788-788` | — | — |
| `MDocMacro.EnvironmentVariable.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:789-789` | — | — |
| `MDocMacro.EnvironmentVariable.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:790-790` | — | — |
| `MDocMacro.EnvironmentVariable.init(name:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:794-794` | — | — |
| `MDocMacro.FilePath` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:807-807` | — | — |
| `MDocMacro.FilePath.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:808-808` | — | — |
| `MDocMacro.FilePath.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:809-809` | — | — |
| `MDocMacro.FilePath.init(path:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:814-814` | — | — |
| `MDocMacro.Author` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:879-879` | — | — |
| `MDocMacro.Author.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:880-880` | — | — |
| `MDocMacro.Author.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:881-881` | — | — |
| `MDocMacro.Author.init(name:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:885-885` | — | — |
| `MDocMacro.Author.init(split:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:892-892` | — | — |
| `MDocMacro.Hyperlink` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:904-904` | — | — |
| `MDocMacro.Hyperlink.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:905-905` | — | — |
| `MDocMacro.Hyperlink.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:906-906` | — | — |
| `MDocMacro.Hyperlink.init(url:displayText:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:912-912` | — | — |
| `MDocMacro.MailTo` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:924-924` | — | — |
| `MDocMacro.MailTo.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:925-925` | — | — |
| `MDocMacro.MailTo.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:926-926` | — | — |
| `MDocMacro.MailTo.init(email:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:930-930` | — | — |
| `MDocMacro.Emphasis` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:982-982` | — | — |
| `MDocMacro.Emphasis.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:983-983` | — | — |
| `MDocMacro.Emphasis.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:984-984` | — | — |
| `MDocMacro.Emphasis.init(arguments:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:988-988` | — | — |
| `MDocMacro.Boldface` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1005-1005` | — | — |
| `MDocMacro.Boldface.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1006-1006` | — | — |
| `MDocMacro.Boldface.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1007-1007` | — | — |
| `MDocMacro.Boldface.init(arguments:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1011-1011` | — | — |
| `MDocMacro.NormalText` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1023-1023` | — | — |
| `MDocMacro.NormalText.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1024-1024` | — | — |
| `MDocMacro.NormalText.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1025-1025` | — | — |
| `MDocMacro.NormalText.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1027-1027` | — | — |
| `MDocMacro.BeginFont` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1042-1042` | — | — |
| `MDocMacro.BeginFont.FontStyle` | enum | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1044-1044` | — | — |
| `MDocMacro.BeginFont.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1056-1056` | — | — |
| `MDocMacro.BeginFont.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1057-1057` | — | — |
| `MDocMacro.BeginFont.init(style:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1061-1061` | — | — |
| `MDocMacro.EndFont` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1074-1074` | — | — |
| `MDocMacro.EndFont.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1075-1075` | — | — |
| `MDocMacro.EndFont.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1076-1076` | — | — |
| `MDocMacro.EndFont.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1078-1078` | — | — |
| `MDocMacro.BeginTypographicDoubleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1095-1095` | — | — |
| `MDocMacro.BeginTypographicDoubleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1096-1096` | — | — |
| `MDocMacro.BeginTypographicDoubleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1097-1097` | — | — |
| `MDocMacro.BeginTypographicDoubleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1099-1099` | — | — |
| `MDocMacro.EndTypographicDoubleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1105-1105` | — | — |
| `MDocMacro.EndTypographicDoubleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1106-1106` | — | — |
| `MDocMacro.EndTypographicDoubleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1107-1107` | — | — |
| `MDocMacro.EndTypographicDoubleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1109-1109` | — | — |
| `MDocMacro.BeginTypewriterDoubleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1126-1126` | — | — |
| `MDocMacro.BeginTypewriterDoubleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1127-1127` | — | — |
| `MDocMacro.BeginTypewriterDoubleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1128-1128` | — | — |
| `MDocMacro.BeginTypewriterDoubleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1130-1130` | — | — |
| `MDocMacro.EndTypewriterDoubleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1136-1136` | — | — |
| `MDocMacro.EndTypewriterDoubleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1137-1137` | — | — |
| `MDocMacro.EndTypewriterDoubleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1138-1138` | — | — |
| `MDocMacro.EndTypewriterDoubleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1140-1140` | — | — |
| `MDocMacro.BeginSingleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1155-1155` | — | — |
| `MDocMacro.BeginSingleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1156-1156` | — | — |
| `MDocMacro.BeginSingleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1157-1157` | — | — |
| `MDocMacro.BeginSingleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1159-1159` | — | — |
| `MDocMacro.EndSingleQuotes` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1165-1165` | — | — |
| `MDocMacro.EndSingleQuotes.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1166-1166` | — | — |
| `MDocMacro.EndSingleQuotes.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1167-1167` | — | — |
| `MDocMacro.EndSingleQuotes.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1169-1169` | — | — |
| `MDocMacro.BeginParentheses` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1184-1184` | — | — |
| `MDocMacro.BeginParentheses.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1185-1185` | — | — |
| `MDocMacro.BeginParentheses.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1186-1186` | — | — |
| `MDocMacro.BeginParentheses.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1188-1188` | — | — |
| `MDocMacro.EndParentheses` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1194-1194` | — | — |
| `MDocMacro.EndParentheses.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1195-1195` | — | — |
| `MDocMacro.EndParentheses.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1196-1196` | — | — |
| `MDocMacro.EndParentheses.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1198-1198` | — | — |
| `MDocMacro.BeginSquareBrackets` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1213-1213` | — | — |
| `MDocMacro.BeginSquareBrackets.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1214-1214` | — | — |
| `MDocMacro.BeginSquareBrackets.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1215-1215` | — | — |
| `MDocMacro.BeginSquareBrackets.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1217-1217` | — | — |
| `MDocMacro.EndSquareBrackets` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1223-1223` | — | — |
| `MDocMacro.EndSquareBrackets.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1224-1224` | — | — |
| `MDocMacro.EndSquareBrackets.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1225-1225` | — | — |
| `MDocMacro.EndSquareBrackets.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1227-1227` | — | — |
| `MDocMacro.BeginCurlyBraces` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1242-1242` | — | — |
| `MDocMacro.BeginCurlyBraces.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1243-1243` | — | — |
| `MDocMacro.BeginCurlyBraces.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1244-1244` | — | — |
| `MDocMacro.BeginCurlyBraces.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1246-1246` | — | — |
| `MDocMacro.EndCurlyBraces` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1252-1252` | — | — |
| `MDocMacro.EndCurlyBraces.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1253-1253` | — | — |
| `MDocMacro.EndCurlyBraces.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1254-1254` | — | — |
| `MDocMacro.EndCurlyBraces.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1256-1256` | — | — |
| `MDocMacro.BeginAngleBrackets` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1271-1271` | — | — |
| `MDocMacro.BeginAngleBrackets.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1272-1272` | — | — |
| `MDocMacro.BeginAngleBrackets.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1273-1273` | — | — |
| `MDocMacro.BeginAngleBrackets.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1275-1275` | — | — |
| `MDocMacro.EndAngleBrackets` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1281-1281` | — | — |
| `MDocMacro.EndAngleBrackets.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1282-1282` | — | — |
| `MDocMacro.EndAngleBrackets.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1283-1283` | — | — |
| `MDocMacro.EndAngleBrackets.init()` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1285-1285` | — | — |
| `MDocMacro.ExitStandard` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1303-1303` | — | — |
| `MDocMacro.ExitStandard.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1304-1304` | — | — |
| `MDocMacro.ExitStandard.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1305-1305` | — | — |
| `MDocMacro.ExitStandard.init(utilities:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1311-1311` | — | — |
| `MDocMacro.AttUnix` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1345-1345` | — | — |
| `MDocMacro.AttUnix.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1346-1346` | — | — |
| `MDocMacro.AttUnix.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1347-1347` | — | — |
| `MDocMacro.AttUnix.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1356-1356` | — | — |
| `MDocMacro.BSD` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1370-1370` | — | — |
| `MDocMacro.BSD.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1371-1371` | — | — |
| `MDocMacro.BSD.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1372-1372` | — | — |
| `MDocMacro.BSD.init(name:version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1382-1382` | — | — |
| `MDocMacro.BSDOS` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1397-1397` | — | — |
| `MDocMacro.BSDOS.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1398-1398` | — | — |
| `MDocMacro.BSDOS.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1399-1399` | — | — |
| `MDocMacro.BSDOS.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1404-1404` | — | — |
| `MDocMacro.NetBSD` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1417-1417` | — | — |
| `MDocMacro.NetBSD.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1418-1418` | — | — |
| `MDocMacro.NetBSD.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1419-1419` | — | — |
| `MDocMacro.NetBSD.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1424-1424` | — | — |
| `MDocMacro.FreeBSD` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1437-1437` | — | — |
| `MDocMacro.FreeBSD.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1438-1438` | — | — |
| `MDocMacro.FreeBSD.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1439-1439` | — | — |
| `MDocMacro.FreeBSD.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1444-1444` | — | — |
| `MDocMacro.OpenBSD` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1457-1457` | — | — |
| `MDocMacro.OpenBSD.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1458-1458` | — | — |
| `MDocMacro.OpenBSD.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1459-1459` | — | — |
| `MDocMacro.OpenBSD.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1464-1464` | — | — |
| `MDocMacro.DragonFly` | struct | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1477-1477` | — | — |
| `MDocMacro.DragonFly.kind` | let | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1478-1478` | — | — |
| `MDocMacro.DragonFly.arguments` | var | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1479-1479` | — | — |
| `MDocMacro.DragonFly.init(version:)` | init | public | `Tools/generate-manual/MDoc/MDocMacro.swift:1484-1484` | — | — |
| `MDocSerializationContext` | struct | public | `Tools/generate-manual/MDoc/MDocSerializationContext.swift:13-13` | — | — |
| `MDocSerializationContext.init()` | init | public | `Tools/generate-manual/MDoc/MDocSerializationContext.swift:16-16` | — | — |

## Protocol requirements

| requirement | protocol | kind | declared at |
|---|---|---|---|
| `pluginName` | `GeneratePlugin` | var | `Plugins/GenerateCommon/GeneratePlugin.swift:16-16` |
| `executableName` | `GeneratePlugin` | var | `Plugins/GenerateCommon/GeneratePlugin.swift:17-17` |
| `artifactName` | `GeneratePlugin` | var | `Plugins/GenerateCommon/GeneratePlugin.swift:18-18` |
| `outputDirectory(context:target:)` | `GeneratePlugin` | func | `Plugins/GenerateCommon/GeneratePlugin.swift:20-20` |
| `run()` | `AsyncParsableCommand` | func | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:23-23` |
| `Command` | `AsyncMainProtocol` | associatedtype | `Sources/ArgumentParser/Parsable Types/AsyncParsableCommand.swift:102-102` |
| `name(for:)` | `EnumerableFlag` | func | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:64-64` |
| `help(for:)` | `EnumerableFlag` | func | `Sources/ArgumentParser/Parsable Types/EnumerableFlag.swift:72-72` |
| `init(argument:)` | `ExpressibleByArgument` | init | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:16-16` |
| `defaultValueDescription` | `ExpressibleByArgument` | var | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:20-20` |
| `allValueStrings` | `ExpressibleByArgument` | var | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:28-28` |
| `allValueDescriptions` | `ExpressibleByArgument` | var | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:39-39` |
| `defaultCompletionKind` | `ExpressibleByArgument` | var | `Sources/ArgumentParser/Parsable Types/ExpressibleByArgument.swift:45-45` |
| `init()` | `ParsableArguments` | init | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:19-19` |
| `validate()` | `ParsableArguments` | func | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:25-25` |
| `_errorLabel` | `ParsableArguments` | var | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:30-31` |
| `_errorPrefix` | `ParsableArguments` | var | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:36-36` |
| `argumentSet(for:)` | `ArgumentSetProvider` | func | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:292-292` |
| `_visibility` | `ArgumentSetProvider` | var | `Sources/ArgumentParser/Parsable Types/ParsableArguments.swift:294-294` |
| `configuration` | `ParsableCommand` | var | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:16-16` |
| `_commandName` | `ParsableCommand` | var | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:23-23` |
| `run()` | `ParsableCommand` | func | `Sources/ArgumentParser/Parsable Types/ParsableCommand.swift:32-32` |
| `count` | `ArrayWrapperProtocol` | var | `Sources/ArgumentParser/Parsing/ArgumentDecoder.swift:298-298` |
| `isAtEnd` | `ArrayWrapperProtocol` | var | `Sources/ArgumentParser/Parsing/ArgumentDecoder.swift:299-299` |
| `currentIndex` | `ArrayWrapperProtocol` | var | `Sources/ArgumentParser/Parsing/ArgumentDecoder.swift:300-300` |
| `getNext()` | `ArrayWrapperProtocol` | func | `Sources/ArgumentParser/Parsing/ArgumentDecoder.swift:301-301` |
| `Contained` | `ArgumentDefinitionContainer` | associatedtype | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:364-364` |
| `Initial` | `ArgumentDefinitionContainer` | associatedtype | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:365-365` |
| `helpOptions` | `ArgumentDefinitionContainer` | var | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:367-367` |
| `update(parsedValues:value:key:origin:)` | `ArgumentDefinitionContainer` | func | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:368-368` |
| `defaultValueDescription(_:)` | `ArgumentDefinitionContainerExpressibleByArgument` | func | `Sources/ArgumentParser/Parsing/ArgumentDefinition.swift:378-378` |
| `Value` | `ParsedWrapper` | associatedtype | `Sources/ArgumentParser/Parsing/Parsed.swift:39-39` |
| `_parsedValue` | `ParsedWrapper` | var | `Sources/ArgumentParser/Parsing/Parsed.swift:40-40` |
| `init(_parsedValue:)` | `ParsedWrapper` | init | `Sources/ArgumentParser/Parsing/Parsed.swift:41-41` |
| `init(_parsedValue:)` | `DecodableParsedWrapper` | init | `Sources/ArgumentParser/Parsing/Parsed.swift:50-50` |
| `wrappedType` | `AnyOptionGroup` | var | `Sources/ArgumentParser/Validators/AsyncCompletionsValidator.swift:56-56` |
| `validate(_:parent:)` | `ParsableArgumentsValidator` | func | `Sources/ArgumentParser/Validators/ParsableArgumentsValidation.swift:40-40` |
| `kind` | `ParsableArgumentsValidatorError` | var | `Sources/ArgumentParser/Validators/ParsableArgumentsValidation.swift:50-50` |
| `didValidateExpectation` | `TestableSwiftTestingParsableArguments` | var | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:37-37` |
| `didRunExpectation` | `TestableSwiftTestingParsableCommand` | var | `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift:49-49` |
| `didValidateExpectation` | `TestableParsableArguments` | var | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:53-53` |
| `didRunExpectation` | `TestableParsableCommand` | var | `Sources/ArgumentParserTestHelpers/TestHelpers.swift:72-72` |
| `convert(_:)` | `Convert` | func | `Tests/ArgumentParserEndToEndTests/TransformEndToEndTests.swift:23-23` |
| `ast` | `MDocComponent` | var | `Tools/generate-manual/DSL/Core/MDocComponent.swift:13-13` |
| `body` | `MDocComponent` | var | `Tools/generate-manual/DSL/Core/MDocComponent.swift:14-15` |
| `_serialized(context:)` | `MDocASTNode` | func | `Tools/generate-manual/MDoc/MDocASTNode.swift:21-21` |
| `kind` | `MDocMacroProtocol` | var | `Tools/generate-manual/MDoc/MDocMacro.swift:90-90` |
| `arguments` | `MDocMacroProtocol` | var | `Tools/generate-manual/MDoc/MDocMacro.swift:93-93` |

## Products

| product | product kind | container | declared at |
|---|---|---|---|
| `ArgumentParser` | library | `Package.swift` | `Package.swift:18-19` |
| `GenerateDoccReference` | command plugin | `Package.swift` | `Package.swift:21-22` |
| `GenerateManual` | command plugin | `Package.swift` | `Package.swift:24-25` |

## @main declarations

| @main type | kind | owning target | declared at |
|---|---|---|---|
| `Color` | struct | `color (Package.swift)` | `Examples/color/Color.swift:14-15` |
| `CountLines` | struct | `count-lines (Package.swift)` | `Examples/count-lines/CountLines.swift:15-17` |
| `DefaultAsFlag` | struct | `default-as-flag (Package.swift)` | `Examples/default-as-flag/DefaultAsFlag.swift:14-15` |
| `Math` | struct | `math (Package.swift)` | `Examples/math/Math.swift:14-15` |
| `Repeat` | struct | `repeat (Package.swift)` | `Examples/repeat/Repeat.swift:14-15` |
| `GenerateDoccReferencePlugin` | struct | `GenerateDoccReference (Package.swift)` | `Plugins/GenerateDoccReference/GenerateDoccReference.swift:15-16` |
| `GenerateManualPlugin` | struct | `GenerateManual (Package.swift)` | `Plugins/GenerateManual/GenerateManualPlugin.swift:15-16` |
| `ChangelogAuthors` | struct | `changelog-authors (Package.swift)` | `Tools/changelog-authors/ChangelogAuthors.swift:19-21` |
| `GenerateDoccReference` | struct | `generate-docc-reference (Package.swift)` | `Tools/generate-docc-reference/GenerateDoccReference.swift:47-48` |
| `GenerateManual` | struct | `generate-manual (Package.swift)` | `Tools/generate-manual/GenerateManual.swift:39-40` |

## Residuals

- `parse-error` `Examples/count-lines/CountLines.swift` lines 59-66 — location body; effect enclosing func run
- `non-literal-manifest` `Package.swift` lines 130-144 — construct #if block is outside the literal Package.swift subset and is not evaluated
- `parse-error` `Sources/ArgumentParser/Usage/UsageGenerator.swift` lines 317-317 — location body; effect enclosing var suggestion
- `parse-error` `Sources/ArgumentParser/Usage/UsageGenerator.swift` lines 322-322 — location body; effect enclosing var suggestion
- `parse-error` `Sources/ArgumentParser/Utilities/Mutex.swift` lines 30-30 — location member; effect enclosing struct _Lock
- `conditional-declaration` `Sources/ArgumentParser/Utilities/SwiftExtensions.swift` lines 12-17 — _SendableMetatype is declared in 2 `#if` branches; every branch renders and none is evaluated
- `parse-error` `Sources/ArgumentParserTestHelpers/TestHelpers+SwiftTesting.swift` lines 1-604 — location top level; effect no enclosing declaration
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/AsyncCommandEndToEndTests.swift` lines 15-15 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/AsyncCommandEndToEndTests.swift` lines 57-57, 63-63 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/CustomParsingEndToEndTests.swift` lines 16-16, 17-17, 18-18, 19-19 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/CustomParsingEndToEndTests.swift` lines 71-71, 79-79, 111-111, 136-136, 158-158, 187-187 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultAsFlagEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultAsFlagEndToEndTests.swift` lines 56-56, 79-79, 102-102, 125-125, 158-158, 169-169, 203-204, 211-213, 220-220, 230-230, 242-242, 257-257 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserEndToEndTests/DefaultAsFlagEndToEndTests.swift` lines 182-197 — location declaration header; effect enclosing func implTestDefaultAsFlagWithTerminatorValueBeforeTerminator
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultSubcommandEndToEndTests.swift` lines 18-18 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultSubcommandEndToEndTests.swift` lines 41-41, 59-59, 69-69, 127-127, 136-136, 155-155, 197-197, 224-224, 256-256, 290-290, 300-300, 326-326, 361-361 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultsEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/DefaultsEndToEndTests.swift` lines 32-32, 76-76, 87-87, 98-98, 109-109, 119-119, 129-129, 139-139, 150-150, 161-161, 172-172, 182-182, 192-192, 202-202, 212-212, 259-259, 272-272, 285-285, 320-320, 339-339, 367-367, 411-411, 436-436, 471-471, 480-481, 495-495, 508-508, 518-518, 536-536, 577-578, 587-588, 605-605, 615-615, 627-627, 644-644, 663-663, 696-696, 704-704, 718-718, 748-749, 758-759, 776-776, 807-807, 858-858, 866-866, 875-875, 884-884, 892-892, 901-901, 910-910, 918-918, 926-926, 935-935, 943-943, 951-951, 960-960, 966-966, 974-974, 991-991, 1025-1025, 1046-1046, 1055-1055 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/EnumEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/EnumEndToEndTests.swift` lines 32-32, 41-41, 47-47, 69-69, 80-80, 91-91 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/EqualsEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/EqualsEndToEndTests.swift` lines 28-28, 36-36, 44-44, 58-58, 75-75, 100-100, 108-108, 117-117, 126-126 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/FlagsEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/FlagsEndToEndTests.swift` lines 36-36, 44-44, 64-64, 109-109, 118-118, 133-133, 148-148, 212-212, 232-232, 252-252, 278-278, 297-297, 330-330, 355-355, 375-375 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/JoinedEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/JoinedEndToEndTests.swift` lines 33-33, 89-89, 107-107, 125-125, 143-143, 163-163, 182-182, 199-199 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/LongNameWithShortDashEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/LongNameWithShortDashEndToEndTests.swift` lines 33-33, 41-41, 49-49, 57-57, 65-65, 73-73, 81-81, 89-89, 97-97, 105-105, 119-119, 132-132 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/NestedCommandEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/NestedCommandEndToEndTests.swift` lines 67-67, 212-212, 219-219, 307-307 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserEndToEndTests/NestedCommandEndToEndTests.swift` lines 58-58 — location declaration header; effect enclosing func expectParseFooCommand
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/OptionGroupEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/OptionGroupEndToEndTests.swift` lines 65-65, 93-93, 116-116, 151-151, 162-162, 175-175, 188-188 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/OptionalEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/OptionalEndToEndTests.swift` lines 30-30, 72-72, 82-82, 92-92, 102-102, 111-111, 120-120, 129-129, 139-139, 149-149, 159-159, 168-168, 177-177, 186-186, 195-195, 205-205 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/PositionalEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/PositionalEndToEndTests.swift` lines 26-26, 47-47, 63-63, 86-86, 110-110, 132-132, 155-155, 187-187, 209-209, 231-231, 275-275 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/RawRepresentableEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/RawRepresentableEndToEndTests.swift` lines 30-30, 36-36, 43-43 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/RepeatingEndToEndTests+ParsingStrategy.swift` lines 34-34, 52-52, 74-74, 97-97, 118-118, 139-139, 170-170, 194-194, 307-307 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/RepeatingEndToEndTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/RepeatingEndToEndTests.swift` lines 27-27, 54-54, 76-76, 83-83, 90-90, 97-97, 104-104, 111-111, 118-118, 126-126, 153-153, 174-174, 255-255, 293-293, 349-349, 380-380, 421-421 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/ShortNameEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/ShortNameEndToEndTests.swift` lines 33-33, 47-47, 67-67, 103-103 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SimpleEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SimpleEndToEndTests.swift` lines 26-26, 35-35, 72-72, 78-78, 116-116, 123-123, 130-130 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SingleValueParsingStrategyTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SingleValueParsingStrategyTests.swift` lines 28-28, 38-38, 48-48, 69-69, 79-79, 90-90 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SourceCompatEndToEndTests.swift` lines 18-18 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SourceCompatEndToEndTests.swift` lines 228-228 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SubcommandEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/SubcommandEndToEndTests.swift` lines 45-45, 61-61, 74-74, 118-118, 155-155, 230-230, 283-283 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/TransformEndToEndTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/TransformEndToEndTests.swift` lines 74-74, 80-80, 87-87, 96-96, 104-104, 112-112, 162-162, 168-168, 175-175, 184-184, 190-190, 197-197 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/UnparsedValuesEndToEndTest.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/UnparsedValuesEndToEndTest.swift` lines 35-35, 54-54, 92-92, 122-122, 172-172, 193-193, 229-229, 258-258, 326-326, 354-354, 379-379 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/ValidationEndToEndTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserEndToEndTests/ValidationEndToEndTests.swift` lines 104-104, 116-116, 121-121, 145-145, 152-152, 180-180, 203-204 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/CountLinesExampleTests.swift` lines 20-22 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/CountLinesExampleTests.swift` lines 27-27, 37-37 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/MathExampleTests.swift` lines 17-19 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/MathExampleTests.swift` lines 24-24, 30-30, 54-54, 83-83, 109-109, 146-146, 158-158, 170-170, 189-189, 217-219, 225-227, 233-235, 241-241, 245-245, 249-249 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/RepeatExampleTests.swift` lines 17-19 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/RepeatExampleTests.swift` lines 24-24, 34-34, 44-44, 58-58, 77-77 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/RollDiceExampleTests.swift` lines 17-19 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserExampleTests/RollDiceExampleTests.swift` lines 24-24, 28-28, 47-47 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserGenerateDoccReferenceTests/GenerateDoccReferenceTests.swift` lines 15-15 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserGenerateDoccReferenceTests/GenerateDoccReferenceTests.swift` lines 17-17, 22-22, 28-28, 31-31, 35-35, 38-38, 42-42, 45-45, 49-49, 52-52 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserGenerateDoccReferenceTests/GenerateDoccReferenceTests.swift` lines 16-16 — location member; effect enclosing struct GenerateDoccReferenceTests
- `macro-not-expanded` `Tests/ArgumentParserGenerateManualTests/GenerateManualTests.swift` lines 15-15 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserGenerateManualTests/GenerateManualTests.swift` lines 17-17, 22-22, 28-28, 32-32, 36-36, 40-40, 44-44, 48-48, 52-52, 56-56, 60-60, 64-64 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserGenerateManualTests/GenerateManualTests.swift` lines 16-16 — location member; effect enclosing struct GenerateManualTests
- `macro-not-expanded` `Tests/ArgumentParserPackageManagerTests/HelpTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserPackageManagerTests/HelpTests.swift` lines 55-55, 73-73, 92-92, 100-100, 117-117, 190-190, 203-203, 228-228, 274-274, 282-282, 290-290 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserPackageManagerTests/HelpTests.swift` lines 25-25 — location declaration header; effect enclosing func getErrorText
- `parse-error` `Tests/ArgumentParserPackageManagerTests/HelpTests.swift` lines 38-38 — location declaration header; effect enclosing func getErrorText
- `macro-not-expanded` `Tests/ArgumentParserPackageManagerTests/Tests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserPackageManagerTests/Tests.swift` lines 24-24, 47-47, 67-67, 88-88 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserToolInfoTests/ArgumentParserToolInfoTests.swift` lines 64-64 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserToolInfoTests/ArgumentParserToolInfoTests.swift` lines 73-74 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/CompletionScriptTests.swift` lines 114-114, 127-127, 142-142, 267-267, 271-271, 275-275 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserUnitTests/CompletionScriptTests.swift` lines 194-194 — location declaration header; effect enclosing func expectCustomCompletion
- `parse-error` `Tests/ArgumentParserUnitTests/CompletionScriptTests.swift` lines 224-265 — location member; effect enclosing extension SerializedTests.CompletionScriptTests
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DefaultAsFlagCompletionTests.swift` lines 18-18 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DefaultAsFlagCompletionTests.swift` lines 22-22, 30-30, 38-38 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DefaultAsFlagDumpHelpTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DefaultAsFlagDumpHelpTests.swift` lines 18-18, 22-22 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DumpHelpGenerationTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/DumpHelpGenerationTests.swift` lines 18-18, 22-22, 26-26, 30-32, 36-38, 42-44, 48-50 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ErrorMessageTests.swift` lines 24-24 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ErrorMessageTests.swift` lines 77-78, 171-172, 176-176, 183-183, 198-198, 214-214, 246-247, 285-286, 347-348, 352-352, 393-394, 407-407 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ExitCodeTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ExitCodeTests.swift` lines 29-29, 55-55, 85-85 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests+AtArgument.swift` lines 67-67, 83-83, 99-99, 115-115, 131-131, 147-147, 163-163, 179-179, 249-249, 265-265, 281-281, 297-297, 313-313, 329-329, 345-345, 414-414, 430-430, 446-446, 462-462, 478-478, 494-494, 510-510, 526-526 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests+AtOption.swift` lines 67-67, 80-80, 93-93, 106-106, 119-119, 132-132, 145-145, 158-158, 225-225, 238-238, 251-251, 264-264, 277-277, 290-290, 303-303, 369-369, 382-382, 395-395, 408-408, 421-421, 434-434, 447-447, 460-460 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests+AtOptionDefaultAsFlag.swift` lines 39-39, 76-76, 105-105 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests+GroupName.swift` lines 78-78, 143-143, 204-204, 249-249, 296-296, 395-395, 420-420 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests+HelpBanner.swift` lines 58-58, 80-80, 96-96, 117-117, 133-133, 153-153, 180-180, 206-206 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift` lines 18-20 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/HelpGenerationTests.swift` lines 44-44, 69-69, 110-110, 135-135, 218-218, 275-275, 343-343, 385-385, 403-403, 431-431, 457-457, 478-478, 514-514, 558-558, 588-588, 627-627, 729-729, 739-739, 777-777, 785-785, 818-818, 855-855, 916-916, 993-993, 1029-1029, 1086-1086, 1108-1108, 1132-1132, 1157-1157, 1181-1181, 1202-1202, 1224-1224, 1267-1267, 1307-1307, 1343-1343, 1387-1387, 1421-1421, 1454-1454, 1520-1520 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/InputOriginTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/InputOriginTests.swift` lines 26-55 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/MirrorTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/MirrorTests.swift` lines 23-30 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/NameSpecificationTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/NameSpecificationTests.swift` lines 20-20, 51-51 — attached macro @Test is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserUnitTests/NameSpecificationTests.swift` lines 1-273 — location top level; effect no enclosing declaration
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ParsableArgumentsValidationTests.swift` lines 17-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/ParsableArgumentsValidationTests.swift` lines 84-84, 134-134, 191-191, 228-228, 253-253, 332-332, 377-377, 393-393, 424-424, 461-461, 495-495, 540-540, 555-555, 590-590, 624-624, 648-648 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/SendableTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/SequenceExtensionTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/SequenceExtensionTests.swift` lines 18-18, 25-25 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/SerializedCompletionSuites.swift` lines 23-26 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/SerializedTestSuite.swift` lines 14-16 — attached macro @Suite is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserUnitTests/SplitArgumentTests.swift` lines 25-793 — location top level; effect no enclosing declaration
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringEditDistanceTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringEditDistanceTests.swift` lines 17-17 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringSnakeCaseTests.swift` lines 16-17 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringSnakeCaseTests.swift` lines 18-60, 64-106 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringWrappingTests.swift` lines 44-44 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/StringWrappingTests.swift` lines 45-45, 71-71, 85-85, 127-127, 148-148, 161-161, 174-174, 194-194 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/TreeTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/TreeTests.swift` lines 34-34, 43-43, 89-89, 94-94 — attached macro @Test is not expanded; code it generates does not render
- `macro-not-expanded` `Tests/ArgumentParserUnitTests/UsageGenerationTests.swift` lines 16-16 — attached macro @Suite is not expanded; code it generates does not render
- `parse-error` `Tests/ArgumentParserUnitTests/UsageGenerationTests.swift` lines 18-247 — location top level; effect no enclosing declaration
