package synchrono.udf;

import java.nio.charset.StandardCharsets;

/** DuckDB jaro_winkler_similarity (duckdb_jaro_winkler, bytes, prefix weight 0.1, no cutoff). */
public class JaroWinkler {

    public final Double evaluate(String a, String b) {
        if (a == null || b == null) {
            return null;
        }
        return similarity(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    public static double similarity(byte[] p, byte[] t) {
        int maxPrefix = Math.min(Math.min(p.length, t.length), 4);
        int prefix = 0;
        while (prefix < maxPrefix && t[prefix] == p[prefix]) {
            prefix++;
        }
        double sim = jaro(p, t);
        if (sim > 0.7) {
            sim += (double) prefix * 0.1 * (1.0 - sim);
        }
        return sim;
    }

    static double jaro(byte[] p, byte[] t) {
        int pLen = p.length;
        int tLen = t.length;
        if (pLen == 0 || tLen == 0) {
            return 0.0;
        }
        if (pLen == 1 && tLen == 1) {
            return p[0] == t[0] ? 1.0 : 0.0;
        }
        int pEnd = pLen;
        int tEnd = tLen;
        int bound;
        if (tLen > pLen) {
            bound = tLen / 2 - 1;
            if (tLen > pLen + bound) {
                tEnd = pLen + bound;
            }
        } else {
            bound = pLen / 2 - 1;
            if (pLen > tLen + bound) {
                pEnd = tLen + bound;
            }
        }
        int start = 0;
        int limit = Math.min(pEnd, tEnd);
        while (start < limit && p[start] == t[start]) {
            start++;
        }
        long common = start;
        long transpositions = 0;
        int pView = pEnd - start;
        int tView = tEnd - start;
        if (pView > 0 && tView > 0) {
            boolean[] pFlag = new boolean[pView];
            boolean[] tFlag = new boolean[tView];
            long flagged = 0;
            for (int j = 0; j < tView; j++) {
                int low = Math.max(0, j - bound);
                int high = Math.min(pView - 1, j + bound);
                byte c = t[start + j];
                for (int i = low; i <= high; i++) {
                    if (!pFlag[i] && p[start + i] == c) {
                        pFlag[i] = true;
                        tFlag[j] = true;
                        flagged++;
                        break;
                    }
                }
            }
            common += flagged;
            if (common == 0) {
                return 0.0;
            }
            int k = 0;
            for (int j = 0; j < tView; j++) {
                if (!tFlag[j]) {
                    continue;
                }
                while (!pFlag[k]) {
                    k++;
                }
                if (p[start + k] != t[start + j]) {
                    transpositions++;
                }
                k++;
            }
        }
        transpositions /= 2;
        double sim = 0;
        sim += (double) common / (double) pLen;
        sim += (double) common / (double) tLen;
        sim += ((double) common - (double) transpositions) / (double) common;
        return sim / 3.0;
    }
}
