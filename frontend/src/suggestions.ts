export type SourceId = "bps" | "bnpb" | "bmkg";

export type SourceInfo = {
  id: SourceId;
  name: string;
  coverage: string;
  questions: string[];
};

export const sourceCatalog: SourceInfo[] = [
  {
    id: "bps",
    name: "Badan Pusat Statistik",
    coverage: "Tabel statistik resmi dan publikasi Sensus Penduduk 2020.",
    questions: ["Berapa perubahan kepadatan penduduk DKI Jakarta dari 2024 ke 2025 menurut tabel kependudukan BPS?"],
  },
  {
    id: "bnpb",
    name: "Badan Nasional Penanggulangan Bencana",
    coverage: "Riwayat kejadian dan dampak bencana per tahun, 2010–2024.",
    questions: ["Bagaimana jumlah kejadian banjir nasional menurut BNPB dari 2019 sampai 2024?"],
  },
  {
    id: "bmkg",
    name: "Badan Meteorologi, Klimatologi, dan Geofisika",
    coverage: "Gempa terbaru, prakiraan cuaca tiga hari, dan peringatan dini aktif.",
    questions: ["Apa gempa terbaru menurut BMKG?", "Apakah ada peringatan dini cuaca aktif untuk Jakarta menurut BMKG?"],
  },
];
