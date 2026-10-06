defmodule MyApp.Accounts.User do
  use Ecto.Schema

  @primary_key {:id, :binary_id, autogenerate: true}
  @foreign_key_type :binary_id

  schema "users" do
    field :email, :string
    field :name, :string, default: "a|b"
    field :role, Ecto.Enum, values: [:member, :admin]
    field :tags, {:array, :string}

    belongs_to :account, MyApp.Accounts.Account
    has_many :posts, MyApp.Content.Post

    timestamps()
  end
end
