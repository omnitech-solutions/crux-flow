// Sequelize model — sequelize.define("Product", {...}) attribute map (point 2).
const { DataTypes } = require("sequelize");
const sequelize = require("../db");

const Product = sequelize.define("Product", {
  id: { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  name: { type: DataTypes.STRING, allowNull: false },
  price: { type: DataTypes.DECIMAL, defaultValue: 0 },
  categoryId: {
    type: DataTypes.INTEGER,
    references: { model: "categories", key: "id" },
  },
});

module.exports = Product;
