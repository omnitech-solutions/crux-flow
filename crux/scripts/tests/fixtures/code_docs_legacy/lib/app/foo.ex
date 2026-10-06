defmodule App.Foo do
  @moduledoc """
  The Foo module does foo things.
  """

  @doc """
  Adds two numbers.
  """
  @spec add(integer(), integer()) :: integer()
  def add(a, b) do
    a + b
  end

  @doc "Subtracts b from a."
  def sub(a, b) do
    a - b
  end
end
