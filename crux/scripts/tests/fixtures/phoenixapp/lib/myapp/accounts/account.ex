defmodule MyApp.Accounts.Account do
  use Ecto.Schema

  schema "accounts" do
    field :name, :string
    field :active, :boolean, default: true

    timestamps()
  end
end
