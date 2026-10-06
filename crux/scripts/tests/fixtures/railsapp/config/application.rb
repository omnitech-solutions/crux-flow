require_relative "boot"
require "rails/all"

module Railsapp
  class Application < Rails::Application
    config.load_defaults 7.0
    # Conventional autoload roots (app/* + lib) — no custom autoload_paths here.
  end
end
