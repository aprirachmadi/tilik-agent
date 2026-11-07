export type SourceId = "bps" | "bnpb" | "bmkg" | "bapanas" | "sipongi";

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
    questions: ["Bagaimana perkembangan jumlah kejadian banjir nasional dari 2019 hingga 2024 menurut BNPB?"],
  },
  {
    id: "bmkg",
    name: "Badan Meteorologi, Klimatologi, dan Geofisika",
    coverage: "Gempa terbaru, prakiraan cuaca tiga hari, dan peringatan dini aktif.",
    questions: ["Kapan dan di mana gempa bumi terbaru terjadi menurut BMKG, dan berapa magnitudonya?", "Apakah saat ini ada peringatan dini cuaca dari BMKG yang masih berlaku untuk wilayah DKI Jakarta?"],
  },
  {
    id: "bapanas",
    name: "Badan Pangan Nasional",
    coverage: "Harga pangan harian tingkat konsumen dan rata-rata bulanan per provinsi sejak Juni 2021.",
    questions: ["Berapa harga cabai rawit merah hari ini di Jawa Barat menurut Bapanas?", "Bagaimana harga beras premium nasional per bulan sepanjang 2025 menurut Bapanas?"],
  },
  {
    id: "sipongi",
    name: "SiPongi Kementerian Kehutanan",
    coverage: "Titik panas satelit 12–48 jam terakhir dan jumlah bulanan per provinsi sejak 2015.",
    questions: ["Berapa jumlah titik panas yang terdeteksi di Indonesia dalam 24 jam terakhir menurut SiPongi?", "Bagaimana perkembangan jumlah titik panas bulanan di Provinsi Kalimantan Barat sepanjang 2025 menurut SiPongi?"],
  },
];
