defmodule MyAppWeb.Router do
  use Phoenix.Router

  # A commented-out route must NOT be parsed.
  # get "/ghost", GhostController, :index

  pipeline :browser do
    plug :accepts, ["html"]
  end

  pipeline :api do
    plug :accepts, ["json"]
  end

  scope "/", MyAppWeb do
    pipe_through :browser

    get "/", PageController, :index
    post "/contact", PageController, :contact
    live "/dashboard", DashboardLive, :index

    resources "/users", UserController do
      resources "/posts", PostController
    end
  end

  scope "/api", MyAppWeb.Api do
    pipe_through :api

    resources "/widgets", WidgetController, only: [:index, :show]
    resources "/gadgets", GadgetController, except: [:delete]
  end
end
