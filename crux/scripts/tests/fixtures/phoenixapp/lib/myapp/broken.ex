defmodule MyApp.Broken do
  @moduledoc "A file with an unterminated sigil to prove the fail-closed bail-out."

  def pattern do
    ~r/this regex sigil never closes and there is no delimiter to end it
  end
end
