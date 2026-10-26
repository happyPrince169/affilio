import base from "@affilio/config/prettier";

/** @type {import("prettier").Config} */
export default {
  ...base,
  tailwindStylesheet: "./apps/web/src/app/globals.css",
};
