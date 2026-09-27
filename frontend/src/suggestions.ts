export type Suggestion = {
  id: string;
  question: string;
};

export const suggestions: Suggestion[] = [
  {
    id: "1",
    question:
      "Berapa perubahan kepadatan penduduk DKI Jakarta dari 2024 ke 2025 menurut tabel kependudukan BPS?",
  },
  {
    id: "3",
    question:
      "Berapa selisih TPT Provinsi Jawa Barat antara Februari dan Agustus 2025, dan pada bulan mana nilainya lebih tinggi?",
  },
  {
    id: "7",
    question:
      "Provinsi mana yang memiliki selisih angka harapan hidup perempuan dan laki-laki terbesar pada 2024, dan berapa nilai kedua angka harapan hidup tersebut?",
  },
  {
    id: "10",
    question:
      "Pada 2025, tiga provinsi mana yang memiliki kesenjangan terbesar antara akses sumber air minum layak dan akses sanitasi layak di perdesaan? Berapa kesenjangan dalam poin persentase di masing-masing provinsi?",
  },
];
