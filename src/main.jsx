import React from "react";
import ReactDOM from "react-dom/client";
import { CssBaseline, ThemeProvider, createTheme } from "@mui/material";
import App from "./App";

const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#005f73" },
    secondary: { main: "#ca6702" },
    background: { default: "#f7f4ea", paper: "#fffdf7" }
  },
  typography: {
    fontFamily: '"Space Grotesk", sans-serif',
    h3: { fontWeight: 700 },
    h5: { fontWeight: 700 }
  },
  shape: { borderRadius: 18 }
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
