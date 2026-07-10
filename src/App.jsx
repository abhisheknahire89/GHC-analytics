import { useEffect, useState } from "react";
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

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "0%";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "0";
  return Number(value).toLocaleString();
}

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

function SummaryCard({ title, body }) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 1.5 }}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {body}
        </Typography>
      </CardContent>
    </Card>
  );
}

function ActionListCard({ title, items }) {
  return (
    <Card sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 1.5 }}>
          {title}
        </Typography>
        <Stack spacing={1.25}>
          {items.map((item) => (
            <Typography key={item} variant="body2" color="text.secondary">
              • {item}
            </Typography>
          ))}
        </Stack>
      </CardContent>
    </Card>
  );
}

function CohortMatrix({ rows }) {
  if (!rows?.length) return null;
  const intervals = [...new Set(rows.map((row) => Number(row.interval_index)))].sort((a, b) => a - b);
  const grouped = rows.reduce((acc, row) => {
    acc[row.cohort_label] ??= {};
    acc[row.cohort_label][row.interval_index] = row;
    return acc;
  }, {});

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Cohort Retention Table
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Cohort</TableCell>
                {intervals.map((interval) => (
                  <TableCell key={interval}>{`M/W ${interval}`}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(grouped).map(([label, intervalMap]) => (
                <TableRow key={label}>
                  <TableCell>{label}</TableCell>
                  {intervals.map((interval) => {
                    const cell = intervalMap[interval];
                    return (
                      <TableCell key={interval}>
                        {cell ? `${formatNumber(cell.retained_count)} (${formatPercent(cell.retention_rate)})` : "—"}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

function RepeatRateCards({ rows }) {
  if (!rows?.length) return null;
  return (
    <Grid container spacing={2}>
      {rows.map((row) => (
        <Grid item xs={12} md={4} key={row.window_days}>
          <Card sx={{ height: "100%", background: "linear-gradient(180deg, #ecfeff, #cffafe)" }}>
            <CardContent>
              <Typography variant="overline">{`${row.window_days}-Day Repeat Rate`}</Typography>
              <Typography variant="h4" sx={{ my: 1 }}>
                {formatPercent(row.rate)}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {formatNumber(row.repeat_customers)} of {formatNumber(row.total_customers)} customers bought again.
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      ))}
    </Grid>
  );
}

function SegmentComparison({ rows }) {
  if (!rows?.length) return null;
  const groups = {
    discount_usage: "Discount On First Order vs No Discount",
    basket_size: "Large First Basket vs Small First Basket"
  };

  return (
    <Grid container spacing={2}>
      {Object.entries(groups).map(([groupKey, title]) => {
        const groupRows = rows.filter((row) => row.segment_group === groupKey);
        if (!groupRows.length) return null;
        return (
          <Grid item xs={12} md={6} key={groupKey}>
            <Card sx={{ height: "100%" }}>
              <CardContent>
                <Typography variant="h6" sx={{ mb: 2 }}>
                  {title}
                </Typography>
                <Stack spacing={1.5}>
                  {groupRows.map((row) => (
                    <Box key={row.segment} sx={{ p: 1.5, borderRadius: 2, bgcolor: "rgba(0,0,0,0.03)" }}>
                      <Typography variant="subtitle2">{row.segment}</Typography>
                      <Typography variant="body2" color="text.secondary">
                        {formatNumber(row.customers)} customers
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Half come back in about {row.median_days} days. Middle range: {row.p25_days} to {row.p75_days} days.
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        );
      })}
    </Grid>
  );
}

function DiscountPerformance({ rows }) {
  if (!rows?.length) return null;
  const topRows = rows.slice(0, 8);
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Retention By Discount Type
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Discount Type</TableCell>
                <TableCell>Customers</TableCell>
                <TableCell>30-Day Repeat</TableCell>
                <TableCell>Time To 2nd Order</TableCell>
                <TableCell>Avg Orders In 90 Days</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {topRows.map((row) => (
                <TableRow key={row.discount_type}>
                  <TableCell>{row.discount_type}</TableCell>
                  <TableCell>{formatNumber(row.customers)}</TableCell>
                  <TableCell>{formatPercent(row.rpr_30)}</TableCell>
                  <TableCell>{row.median_days_to_second_order ?? "—"} days</TableCell>
                  <TableCell>{row.avg_orders_90d}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}

function PastAnalyses({ items, activeId, onOpen }) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 1 }}>Past Analyses</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Reopen a saved analysis without uploading or processing the CSV again.
        </Typography>
        <Stack spacing={1}>
          {items.length ? items.map((item) => (
            <Box key={item.id} sx={{ alignItems: { xs: "flex-start", sm: "center" }, border: "1px solid", borderColor: item.id === activeId ? "primary.main" : "divider", borderRadius: 2, display: "flex", gap: 2, justifyContent: "space-between", p: 1.5 }}>
              <Box>
                <Typography variant="subtitle2">{item.source_filename}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {new Date(item.created_at).toLocaleString()} · 30-day repeat: {formatPercent(item.rpr_30)}
                </Typography>
              </Box>
              <Stack direction="row" spacing={1}>
                <Button size="small" onClick={() => onOpen(item.id)}>Open</Button>
                <Button size="small" component="a" href={`${API_BASE}/analyses/${item.id}/export-pdf`}>PDF</Button>
              </Stack>
            </Box>
          )) : <Typography variant="body2" color="text.secondary">No saved analyses yet.</Typography>}
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function App() {
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);

  const loadHistory = async () => {
    const response = await fetch(`${API_BASE}/analyses`);
    if (!response.ok) throw new Error("Could not load past analyses.");
    setHistory(await response.json());
  };

  useEffect(() => {
    loadHistory().catch(() => setHistory([]));
  }, []);

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
        body: JSON.stringify(uploadData)
      });
      if (!analyzeRes.ok) throw new Error(await analyzeRes.text());
      setResult(await analyzeRes.json());
      await loadHistory();
    } catch (err) {
      setError(err.message || "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const openAnalysis = async (analysisId) => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}`);
      if (!response.ok) throw new Error(await response.text());
      setResult(await response.json());
    } catch (err) {
      setError(err.message || "Could not open analysis");
    } finally {
      setLoading(false);
    }
  };

  const quality = result?.data_quality ?? {};
  const explanations = result?.ui_explanations ?? {};
  const intelligence = result?.analytics_intelligence ?? {};
  const plainLanguage = result?.plain_language_report ?? {};
  const cohortRetention = result?.cohort_retention ?? [];
  const repeatRates = result?.repeat_purchase_rates ?? [];
  const timeToSecond = result?.time_to_second_segments ?? [];
  const discountRetention = result?.retention_by_discount ?? [];

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

          <PastAnalyses items={history} activeId={result?.analysis_id} onOpen={openAnalysis} />

          {error ? <Alert severity="error">{error}</Alert> : null}

          {result ? (
            <>
              <Card sx={{ background: "linear-gradient(135deg, #9a3412, #ca6702)", color: "white" }}>
                <CardContent>
                  <Typography variant="h5" sx={{ mb: 2 }}>
                    What is happening?
                  </Typography>
                  <Typography variant="body1" sx={{ lineHeight: 1.8 }}>
                    {plainLanguage.what_is_happening}
                  </Typography>
                </CardContent>
              </Card>

              <Grid container spacing={2}>
                <Grid item xs={12} md={8}>
                  <ActionListCard
                    title="What should we do next?"
                    items={plainLanguage.what_should_we_do_next ?? []}
                  />
                </Grid>
                <Grid item xs={12} md={4}>
                  <Card sx={{ height: "100%", background: "linear-gradient(180deg, #fff7ed, #ffedd5)" }}>
                    <CardContent>
                      <Typography variant="h6" sx={{ mb: 1.5 }}>
                        Recommended Target
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {plainLanguage.target_line}
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              </Grid>

              <RepeatRateCards rows={repeatRates} />
              <CohortMatrix rows={cohortRetention} />
              <SegmentComparison rows={timeToSecond} />
              <DiscountPerformance rows={discountRetention} />

              <Card sx={{ background: "linear-gradient(135deg, #005f73, #0a9396)", color: "white" }}>
                <CardContent>
                  <Typography variant="overline" sx={{ opacity: 0.9 }}>
                    Primary Insight
                  </Typography>
                  <Typography variant="h5" sx={{ mt: 1, mb: 2 }}>
                    {explanations.primary_insight}
                  </Typography>
                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                    {(explanations.metric_tags ?? []).map((tag) => (
                      <Chip
                        key={tag}
                        label={tag}
                        size="small"
                        sx={{ bgcolor: "rgba(255,255,255,0.18)", color: "white" }}
                      />
                    ))}
                  </Stack>
                </CardContent>
              </Card>

              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <SummaryCard title="Cohort Summary" body={explanations.cohort_summary} />
                </Grid>
                <Grid item xs={12} md={6}>
                  <SummaryCard title="Repeat Purchase Summary" body={explanations.repeat_purchase_summary} />
                </Grid>
                <Grid item xs={12} md={6}>
                  <SummaryCard title="Segment Summary" body={explanations.segment_summary} />
                </Grid>
                <Grid item xs={12} md={6}>
                  <SummaryCard title="Discount Summary" body={explanations.discount_summary} />
                </Grid>
              </Grid>

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
                    {result.analysis_id ? (
                      <Button component="a" href={`${API_BASE}/analyses/${result.analysis_id}/export-pdf`} variant="contained" size="small">
                        Download PDF
                      </Button>
                    ) : null}
                  </Stack>
                </CardContent>
              </Card>

              <Card>
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 2 }}>
                    Analytics Intelligence
                  </Typography>
                  <Grid container spacing={2}>
                    {(intelligence.insights ?? []).map((insight) => (
                      <Grid item xs={12} md={6} key={`${insight.title}-${insight.type}`}>
                        <Card variant="outlined" sx={{ height: "100%" }}>
                          <CardContent>
                            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                              <Typography variant="subtitle1">{insight.title}</Typography>
                              <Chip label={insight.type} size="small" color="secondary" />
                            </Stack>
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                              {insight.metric_reference}
                            </Typography>
                            <Typography variant="body2" sx={{ mb: 1 }}>
                              {insight.suggested_action}
                            </Typography>
                            <Chip label={insight.impact_area} size="small" variant="outlined" />
                          </CardContent>
                        </Card>
                      </Grid>
                    ))}
                  </Grid>
                  <Grid container spacing={2} sx={{ mt: 0.5 }}>
                    <Grid item xs={12} md={6}>
                      <Card variant="outlined" sx={{ height: "100%" }}>
                        <CardContent>
                          <Typography variant="subtitle1" sx={{ mb: 1 }}>
                            Segment To Watch
                          </Typography>
                          <Typography variant="body2" sx={{ mb: 1 }}>
                            <strong>{intelligence.segment_to_watch?.segment_name}</strong>
                            {intelligence.segment_to_watch?.segment_group ? ` (${intelligence.segment_to_watch.segment_group})` : ""}
                          </Typography>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            {intelligence.segment_to_watch?.reason}
                          </Typography>
                          <Typography variant="body2">
                            {intelligence.segment_to_watch?.suggested_experiment}
                          </Typography>
                        </CardContent>
                      </Card>
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <Card variant="outlined" sx={{ height: "100%" }}>
                        <CardContent>
                          <Typography variant="subtitle1" sx={{ mb: 1 }}>
                            Follow-Up Questions
                          </Typography>
                          <Stack spacing={1}>
                            {(intelligence.followup_queries ?? []).map((query) => (
                              <Typography variant="body2" color="text.secondary" key={query}>
                                {query}
                              </Typography>
                            ))}
                          </Stack>
                        </CardContent>
                      </Card>
                    </Grid>
                  </Grid>
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
