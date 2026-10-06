defmodule MyApp.Accounts do
  @moduledoc """
  The Accounts context.

  MyApp.Isolated is named ONLY in this doc string, so the string scrub must
  leave it edge-less — it must never create a phantom module-graph edge.
  """

  alias MyApp.Accounts.{User, Account}
  alias MyApp.Repo, as: DB

  def list_users do
    DB.all(User)
  end

  def get_account(id) do
    DB.get(Account, id)
  end

  def external do
    # MyApp.External.Service is a dependency with no in-repo file → dropped.
    MyApp.External.Service.call()
  end
end
