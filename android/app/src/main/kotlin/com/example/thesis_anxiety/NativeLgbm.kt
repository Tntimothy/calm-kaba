// WHAT THIS FILE DOES
// Bridge to the LightGBM C++ library (cpp/bridge.cpp) so the phone runs
// the exact same models as the Python training code.

package com.example.thesis_anxiety

object NativeLgbm {
    init { System.loadLibrary("calm_lgbm") }
    external fun load(text: String): Long
    external fun predict(handle: Long, features: DoubleArray): Double
    external fun contributions(handle: Long, features: DoubleArray): DoubleArray
    external fun free(handle: Long)
}
