package synchrono.udf;

/** DuckDB round(DOUBLE, precision >= 0): std::round(x * 10^p) / 10^p, halves away from zero. */
public class DuckRound {

    public final Double evaluate(Double x, Integer precision) {
        if (x == null || precision == null) {
            return null;
        }
        return round(x, precision);
    }

    public static double round(double x, int precision) {
        double modifier = Math.pow(10, precision);
        double rounded = roundHalfAway(x * modifier) / modifier;
        return Double.isInfinite(rounded) || Double.isNaN(rounded) ? x : rounded;
    }

    static double roundHalfAway(double v) {
        if (Double.isNaN(v) || Double.isInfinite(v)) {
            return v;
        }
        double floor = Math.floor(v);
        double diff = v - floor;
        if (diff > 0.5) {
            return floor + 1.0;
        }
        if (diff < 0.5) {
            return floor;
        }
        return v >= 0 ? floor + 1.0 : floor;
    }
}
