export const GRADE_LETTERS = { 1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F" };
export const GRADE_IDS = [1, 2, 3, 4, 5, 6];
export const MATCHING_GRADES = [1, 2, 3, 4, 5];

export const ELEMENT_LABELS = {
  nik: "NIK",
  nama: "Nama lengkap",
  tempat_lahir: "Tempat lahir",
  tanggal_lahir: "Tanggal lahir",
  jenis_kelamin: "Jenis kelamin",
  nama_ibu: "Nama ibu kandung",
  wilayah: "Wilayah",
};

export const NIK_COLUMN = {
  wajib: "Kolom NIK harus ada",
  terlarang: "Kolom NIK harus tidak ada",
  abaikan: "Tidak diperiksa",
};

export const NAME_CLEANING = {
  titles: { label: "Buang gelar", example: "Dr. Hj. Siti Aminah, S.Pd. → siti aminah" },
  patronym: { label: "Buang bin/binti", example: "Maulana bin Ahmad → maulana" },
  abbreviations: { label: "Ubah singkatan Muhammad", example: "M. Rizki, Muh Rizki → muhammad rizki" },
};

export const DATE_MATCH = {
  similarity: "Kemiripan teks (Jaro-Winkler) — bawaan engine lama",
  exact: "Harus sama persis (1 bila sama, 0 bila beda)",
};

export const THRESHOLDS = ["autoMissingMax", "autoScoreMin", "reviewMissingCount",
  "reviewScoreMin", "reviewScoreMax"];

export const SOURCE_LABELS = { config: "disimpan lewat API", env: "dari environment", default: "bawaan" };

export const FIELD_DOCS = [
  { group: "Kriteria grading", path: "criteria.order", label: "Urutan evaluasi", type: "bilangan bulat",
    range: "unik per grade", nullMeaning: "—", grades: "A–D",
    effect: "Kriteria dievaluasi dari urutan terkecil; berkas masuk grade pertama yang terpenuhi." },
  { group: "Kriteria grading", path: "criteria.active", label: "Aktif", type: "boolean", range: "true / false",
    nullMeaning: "—", grades: "A–D", effect: "Grade nonaktif dilewati tanpa menghapus konfigurasinya." },
  { group: "Kriteria grading", path: "criteria.nikColumn", label: "Kolom NIK", type: "teks",
    range: "wajib · terlarang · abaikan", nullMeaning: "—", grades: "A–D",
    effect: "Syarat keberadaan kolom NIK. Yang memisahkan A/B (wajib) dari C/D (terlarang)." },
  { group: "Kriteria grading", path: "criteria.minCompleteness.<elemen>", label: "Kelengkapan minimal",
    type: "pecahan", range: "0–1 (0,7 = 70%)", nullMeaning: "tidak diperiksa", grades: "A–D",
    effect: "Porsi baris yang elemennya terisi. Nilai di atas 0 sekaligus mensyaratkan kolomnya ada." },
  { group: "Kriteria grading", path: "criteria.minNikTrusted", label: "NIK tepercaya minimal",
    type: "pecahan", range: "0–1", nullMeaning: "tidak diperiksa", grades: "A–D",
    effect: "Porsi baris ber-NIK tepercaya. Harus null bila kolom NIK terlarang." },
  { group: "Pita skor", path: "score.min / score.max", label: "Rentang skor mutu", type: "bilangan bulat",
    range: "0–100, tidak tumpang tindih", nullMeaning: "—", grades: "A–F",
    effect: "Skor mutu berkas di grade ini dipetakan ke rentang ini." },
  { group: "Pita skor", path: "score.severityLabel", label: "Label", type: "teks", range: "bebas",
    nullMeaning: "—", grades: "A–F", effect: "Ditampilkan di hasil grading." },
  { group: "Pita skor", path: "score.canProceed", label: "Boleh lanjut ke sinkronisasi", type: "boolean",
    range: "true / false", nullMeaning: "—", grades: "A–F",
    effect: "False = berkas tidak dapat dicocokkan (mis. grade F)." },
  { group: "Pita skor", path: "score.criteriaDescription", label: "Deskripsi", type: "teks", range: "bebas",
    nullMeaning: "—", grades: "A–F", effect: "Penjelasan grade di hasil grading." },
  { group: "Ambang matching", path: "matching.autoMissingMax", label: "AUTO: maks elemen kosong",
    type: "bilangan bulat", range: "≥ 0", nullMeaning: "tanpa syarat", grades: "A–F",
    effect: "AUTO hanya bila jumlah elemen kosong tidak melebihi angka ini." },
  { group: "Ambang matching", path: "matching.autoScoreMin", label: "AUTO: skor minimal", type: "angka",
    range: "0–100 (wajib)", nullMeaning: "tidak boleh null", grades: "A–F",
    effect: "Skor kandidat terbaik ≥ nilai ini → AUTO (bila syarat kosong terpenuhi)." },
  { group: "Ambang matching", path: "matching.reviewMissingCount", label: "REVIEW: jumlah elemen kosong",
    type: "bilangan bulat", range: "≥ 0", nullMeaning: "tanpa syarat", grades: "A–F",
    effect: "REVIEW hanya bila jumlah elemen kosong TEPAT angka ini." },
  { group: "Ambang matching", path: "matching.reviewScoreMin / reviewScoreMax", label: "REVIEW: rentang skor",
    type: "angka", range: "0–100, min ≤ max (wajib)", nullMeaning: "tidak boleh null", grades: "A–F",
    effect: "Skor di [min, max) → REVIEW. Di luar AUTO dan REVIEW → UNMATCH." },
  { group: "Skor kemiripan", path: "matching.weights", label: "Bobot per elemen", type: "objek {elemen: persen}",
    range: "jumlah tepat 100; elemen dari availableElements", nullMeaning: "kembali ke bawaan grade",
    grades: "A–E", effect: "Diganti utuh. Urutan dipertahankan (ikut menentukan digit terakhir skor). NIK tidak bisa diberi bobot." },
  { group: "Skor kemiripan", path: "matching.missingElements", label: "Elemen dihitung kosong",
    type: "daftar elemen", range: "elemen dari availableElements", nullMeaning: "kembali ke bawaan grade",
    grades: "A–E", effect: "Diganti utuh. Jumlah elemen kosong dipakai aturan AUTO/REVIEW." },
  { group: "Skor kemiripan", path: "matching.nameCleaning", label: "Pembersihan nama",
    type: "objek {titles, patronym, abbreviations}", range: "true / false per sakelar",
    nullMeaning: "kembali ke bawaan (semua mati)", grades: "A–E",
    effect: "Digabung per sakelar. Berlaku untuk nama & nama ibu, di berkas & master, di semua pass." },
  { group: "Skor kemiripan", path: "matching.dateMatch", label: "Cara menilai tanggal lahir", type: "teks",
    range: "similarity · exact", nullMeaning: "kembali ke similarity", grades: "A–E",
    effect: "similarity = Jaro-Winkler atas teks tanggal; exact = 1 bila sama persis, selain itu 0." },
  { group: "Skor kemiripan", path: "matching.blocking", label: "Kueri blocking", type: "teks",
    range: "hanya dibaca", nullMeaning: "—", grades: "A–E",
    effect: "Menentukan kandidat yang dibandingkan. Diubah lewat basis data/migrasi, bukan API." },
  { group: "Global", path: "global.grading.scoreWeights", label: "Bobot skor mutu grading",
    type: "objek {kelengkapan, nik_tepercaya}", range: "0–1, jumlah 1", nullMeaning: "kembali ke bawaan (0,6/0,4)",
    grades: "semua", effect: "Digabung per kunci. Skor mutu = pita grade × campuran kelengkapan & NIK tepercaya." },
  { group: "Global", path: "global.grading.gradeECombinations", label: "Kombinasi grade E",
    type: "daftar kombinasi elemen", range: "elemen: nama, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu, wilayah",
    nullMeaning: "kembali ke bawaan (4 kombinasi)", grades: "E",
    effect: "Diganti utuh. Berkas yang memenuhi salah satu kombinasi menjadi grade E." },
  { group: "Global", path: "global.matching.conflictEpsilon", label: "Selisih seri (CONFLICT)", type: "angka",
    range: "0–100 (poin skor)", nullMeaning: "kembali ke env/bawaan (0)", grades: "semua",
    effect: "Kandidat yang skornya berselisih ≤ nilai ini dari kandidat teratas dianggap seri → CONFLICT." },
  { group: "Global", path: "global.matching.contradictionJw", label: "Ambang nama ibu bertentangan",
    type: "angka", range: "0–1", nullMeaning: "kembali ke env/bawaan (0,80)", grades: "semua",
    effect: "Di Pass 1, kemiripan nama ibu di bawah nilai ini membatalkan kecocokan NIK." },
];

export function elementLabel(name) {
  return ELEMENT_LABELS[name] || name;
}

export function gradeName(grade) {
  return `Grade ${GRADE_LETTERS[grade.gradeId] || grade.gradeId}`;
}
