# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record.

ActiveRecord::Schema[7.0].define(version: 2024_01_01_000000) do
  create_table "users", force: :cascade do |t|
    t.string "email", null: false
    t.string "name", default: "anon"
    t.string "status", default: "a|b"
    t.index ["email"], name: "index_users_on_email", unique: true
  end

  create_table "posts", force: :cascade do |t|
    t.string "title", null: false
    t.integer "user_id", null: false
    t.integer "author_id"
    t.index ["user_id"], name: "index_posts_on_user_id"
  end

  create_table "comments", force: :cascade do |t|
    t.text "body"
    t.integer "post_id", null: false
  end

  add_foreign_key "posts", "users"
  add_foreign_key "posts", "users", column: "author_id"
  add_foreign_key "comments", "posts"
end
