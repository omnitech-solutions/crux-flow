defmodule MyApp.MixProject do
  use Mix.Project

  def project do
    [
      app: :myapp,
      version: "0.1.0",
      elixir: "~> 1.16"
    ]
  end

  def application do
    [mod: {MyApp.Application, []}]
  end
end
