class User < ApplicationRecord
  belongs_to :account

  # Account and Admin::Setting resolve to in-repo files (edges kept);
  # Widget is a gem constant with no in-repo file (edge dropped) — RESOLVE-OR-DROP.
  #
  # Isolated is named only in this comment, so it must stay edge-less (comment
  # scrub). Thing is named only in the string below, so it must stay edge-less
  # (string scrub). Neither may produce a phantom edge.
  NOTE = "Thing is only a string literal here, never a real reference"

  def linked
    [Account, Admin::Setting, Widget]
  end
end
