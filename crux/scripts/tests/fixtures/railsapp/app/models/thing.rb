class Thing
  # This is app/models/thing.rb. It shares the constant `Thing` with lib/thing.rb;
  # app/* has precedence over lib, so this file wins and lib/thing.rb is shadowed.
end
