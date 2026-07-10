import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  Grid,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography
} from "@mui/material";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

const sectionTitles = {
  cohort_retention: "Cohort Retention",
  repeat_purchase_rates: "Repeat Purchase Rates",
  time_to_second_segments: "Time To 2nd Order Segments",
  retention_by_discount: "Retention By Discount"
};

function MetricCard({ label, value }) {
  return (
    <Card sx={{ height: "100%", background: "linear-gradient(160deg, #fffdf7, #f0efe6)" }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h5" sx={{ mt: 1 }}>
          {value}
        </Typography>
      </CardContent>
    </Card>
  );
}

function DataTable({ title, rows }) {
  if (!rows?.length) return null;
  const columns = Object.keys(rows[0]);
  return (
    <Card sx={{ mt: 3 }}>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          {title}
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                {columns.map((column) => (
                  <TableCell key={column}>{column}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow key={`${title}-${index}`}>
                  {columns.map((column) => (
                    <TableCell key={column}>{String(row[column] ?? "")}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

export default function App() {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const handleAnalyze = async () => {
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);

      const uploadRes = await fetch(`${API_BASE}/upload-csv`, {
        method: "POST",
        body: form
      });
      if (!uploadRes.ok) throw new Error(await uploadRes.text());
      const uploadData = await uploadRes.json();

      const analyzeRes = await fetch(`${API_BASE}/analyze-retention`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: uploadData.file_path })
      });
      if (!analyzeRes.ok) throw new Error(await analyzeRes.text());
      setResult(await analyzeRes.json());
    } catch (err) {
      setError(err.message || "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const quality = result?.data_quality ?? {};

  return (
    <Box
      sx={{
        minHeight: "100vh",
        py: 6,
        background:
          "radial-gradient(circle at top left, rgba(238, 155, 0, 0.22), transparent 30%), linear-gradient(180deg, #f7f4ea 0%, #eef6f4 100%)"
      }}
    >
      <Container maxWidth="lg">
        <Stack spacing={3}>
          <Box>
            <Chip label="GHC Analytics" color="secondary" sx={{ mb: 2 }} />
            <Typography variant="h3">Retention Analytics Studio</Typography>
            <Typography variant="body1" sx={{ mt: 1, maxWidth: 720, color: "text.secondary" }}>
              Upload a transaction CSV, run retention analysis, and export the generated tables for downstream use.
            </Typography>
          </Box>

          <Card sx={{ overflow: "visible" }}>
            <CardContent>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }}>
                <Button variant="outlined" component="label">
                  Choose CSV
                  <input hidden type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                </Button>
                <Typography sx={{ flex: 1 }}>{file ? file.name : "No file selected"}</Typography>
                <Button variant="contained" onClick={handleAnalyze} disabled={!file || loading}>
                  {loading ? <CircularProgress size={22} color="inherit" /> : "Analyze"}
                </Button>
              </Stack>
            </CardContent>
          </Card>

          {error ? <Alert severity="error">{error}</Alert> : null}

          {result ? (
            <>
              <Grid container spacing={2}>
                {Object.entries(quality).map(([key, value]) => (
                  <Grid item xs={6} md={3} key={key}>
                    <MetricCard label={key} value={value} />
                  </Grid>
                ))}
              </Grid>

              <Card>
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 2 }}>
                    Output Files
                  </Typography>
                  <Stack direction={{ xs: "column", md: "row" }} spacing={1} flexWrap="wrap">
                    {Object.entries(result.output_files ?? {}).map(([key, path]) => (
                      <Link key={key} href={`${API_BASE}/${path}`} target="_blank" rel="noreferrer" underline="hover">
                        {key}
                      </Link>
                    ))}
                  </Stack>
                </CardContent>
              </Card>

              {Object.entries(sectionTitles).map(([key, title]) => (
                <DataTable key={key} title={title} rows={result[key]} />
              ))}
            </>
          ) : null}
        </Stack>
      </Container>
    </Box>
  );
}
