Rails.application.routes.draw do
  # A commented-out route must NOT be parsed.
  # get "/ghost", to: "ghosts#index"

  resources :users, only: [:index, :show]
  resource :profile

  namespace :admin do
    resources :reports, except: [:destroy]
  end

  get "/login", to: "sessions#new"
  post "/logout" => "sessions#destroy"
end
