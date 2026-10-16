
#include <omp.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>


static inline uint8_t clamp8(int v) {
    return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
}

static inline float clampf(float v, float lo, float hi) {
    return v < lo ? lo : v > hi ? hi : v;
}


extern "C" {

void apply_grayscale(uint8_t* pix, int w, int h) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int i = (y * w + x) * 3;
            uint8_t g = clamp8((int)(0.299f * pix[i] + 0.587f * pix[i+1] + 0.114f * pix[i+2]));
            pix[i] = pix[i+1] = pix[i+2] = g;
        }
    }
}


void apply_blur(uint8_t* pix, int w, int h, int radius) {
    if (radius < 1) radius = 1;
    int sz = w * h * 3;
    uint8_t* tmp = (uint8_t*)malloc(sz);
    if (!tmp) return;
    memcpy(tmp, pix, sz);

    /* Horizontal pass */
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int sr = 0, sg = 0, sb = 0, cnt = 0;
            for (int dx = -radius; dx <= radius; dx++) {
                int nx = x + dx;
                if (nx >= 0 && nx < w) {
                    int i = (y * w + nx) * 3;
                    sr += tmp[i]; sg += tmp[i+1]; sb += tmp[i+2]; cnt++;
                }
            }
            int i = (y * w + x) * 3;
            pix[i] = sr/cnt; pix[i+1] = sg/cnt; pix[i+2] = sb/cnt;
        }
    }

    /* Copy for vertical pass */
    memcpy(tmp, pix, sz);

    /* Vertical pass */
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int sr = 0, sg = 0, sb = 0, cnt = 0;
            for (int dy = -radius; dy <= radius; dy++) {
                int ny = y + dy;
                if (ny >= 0 && ny < h) {
                    int i = (ny * w + x) * 3;
                    sr += tmp[i]; sg += tmp[i+1]; sb += tmp[i+2]; cnt++;
                }
            }
            int i = (y * w + x) * 3;
            pix[i] = sr/cnt; pix[i+1] = sg/cnt; pix[i+2] = sb/cnt;
        }
    }
    free(tmp);
}

/**
 * apply_edge_detection
 * Sobel operator on luminance; result written back as grayscale edges.
 */
void apply_edge_detection(uint8_t* pix, int w, int h) {
    int sz = w * h * 3;
    uint8_t* tmp = (uint8_t*)malloc(sz);
    if (!tmp) return;
    memcpy(tmp, pix, sz);

    const int Kx[3][3] = {{-1, 0, 1}, {-2, 0, 2}, {-1, 0, 1}};
    const int Ky[3][3] = {{-1,-2,-1}, { 0, 0, 0}, { 1, 2, 1}};

    #pragma omp parallel for schedule(static)
    for (int y = 1; y < h - 1; y++) {
        for (int x = 1; x < w - 1; x++) {
            float gx = 0, gy = 0;
            for (int dy = -1; dy <= 1; dy++) {
                for (int dx = -1; dx <= 1; dx++) {
                    int i = ((y+dy)*w + (x+dx))*3;
                    float lum = 0.299f*tmp[i] + 0.587f*tmp[i+1] + 0.114f*tmp[i+2];
                    gx += lum * Kx[dy+1][dx+1];
                    gy += lum * Ky[dy+1][dx+1];
                }
            }
            uint8_t mag = clamp8((int)sqrtf(gx*gx + gy*gy));
            int i = (y*w + x)*3;
            pix[i] = pix[i+1] = pix[i+2] = mag;
        }
    }
    free(tmp);
}

/**
 * apply_brightness
 * Multiply each channel by factor; clamp to [0,255].
 * Row-parallel; flat array lets us use collapse(2)-style loop.
 */
void apply_brightness(uint8_t* pix, int w, int h, float factor) {
    int total = w * h * 3;
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < total; i++) {
        pix[i] = clamp8((int)((float)pix[i] * factor));
    }
}

/**
 * apply_sharpen
 * Convolution with a 5-centre Laplacian sharpening kernel.
 */
void apply_sharpen(uint8_t* pix, int w, int h) {
    int sz = w * h * 3;
    uint8_t* tmp = (uint8_t*)malloc(sz);
    if (!tmp) return;
    memcpy(tmp, pix, sz);
    const int K[3][3] = {{ 0,-1, 0},{-1, 5,-1},{ 0,-1, 0}};

    #pragma omp parallel for schedule(static)
    for (int y = 1; y < h - 1; y++) {
        for (int x = 1; x < w - 1; x++) {
            for (int c = 0; c < 3; c++) {
                int acc = 0;
                for (int dy = -1; dy <= 1; dy++)
                    for (int dx = -1; dx <= 1; dx++)
                        acc += tmp[((y+dy)*w+(x+dx))*3+c] * K[dy+1][dx+1];
                pix[(y*w+x)*3+c] = clamp8(acc);
            }
        }
    }
    free(tmp);
}

/**
 * apply_invert
 * Per-pixel bitwise complement.
 */
void apply_invert(uint8_t* pix, int w, int h) {
    int total = w * h * 3;
    #pragma omp parallel for schedule(static)
    for (int i = 0; i < total; i++) pix[i] = 255 - pix[i];
}

/**
 * apply_sepia
 * Classic warm-tone matrix transform.
 */
void apply_sepia(uint8_t* pix, int w, int h) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int i = (y*w+x)*3;
            float r = pix[i], g = pix[i+1], b = pix[i+2];
            pix[i]   = clamp8((int)(r*0.393f + g*0.769f + b*0.189f));
            pix[i+1] = clamp8((int)(r*0.349f + g*0.686f + b*0.168f));
            pix[i+2] = clamp8((int)(r*0.272f + g*0.534f + b*0.131f));
        }
    }
}

/* 
   DATA AUGMENTATION
   */

/**
 * augment_hflip — horizontal mirror
 */
void augment_hflip(uint8_t* pix, int w, int h) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w / 2; x++) {
            int a = (y*w+x)*3, b_ = (y*w+(w-1-x))*3;
            for (int c = 0; c < 3; c++) {
                uint8_t t = pix[a+c]; pix[a+c] = pix[b_+c]; pix[b_+c] = t;
            }
        }
    }
}

/**
 * augment_vflip — vertical mirror
 */
void augment_vflip(uint8_t* pix, int w, int h) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h / 2; y++) {
        for (int x = 0; x < w; x++) {
            int a = (y*w+x)*3, b_ = ((h-1-y)*w+x)*3;
            for (int c = 0; c < 3; c++) {
                uint8_t t = pix[a+c]; pix[a+c] = pix[b_+c]; pix[b_+c] = t;
            }
        }
    }
}

/**
 * augment_noise — additive Gaussian noise via Box-Muller
 * Each thread uses its own LCG seeded by row index, so no sync needed.
 */
void augment_noise(uint8_t* pix, int w, int h, float std_dev) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        unsigned int seed = (unsigned int)(y * 1099511629 + 2246822519u);
        for (int x = 0; x < w; x++) {
            int i = (y*w+x)*3;
            for (int c = 0; c < 3; c++) {
                seed = seed * 1664525u + 1013904223u;
                float u1 = (float)(seed & 0xFFFFFF) / (float)0xFFFFFF + 1e-6f;
                seed = seed * 1664525u + 1013904223u;
                float u2 = (float)(seed & 0xFFFFFF) / (float)0xFFFFFF;
                float z  = sqrtf(-2.0f * logf(u1)) * cosf(6.2831853f * u2);
                pix[i+c] = clamp8((int)pix[i+c] + (int)(z * std_dev));
            }
        }
    }
}

/**
 * augment_color_jitter — saturation + brightness randomisation
 */
void augment_color_jitter(uint8_t* pix, int w, int h, float sat, float bright) {
    #pragma omp parallel for schedule(static)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int i = (y*w+x)*3;
            float r = pix[i]/255.0f, g = pix[i+1]/255.0f, b = pix[i+2]/255.0f;
            float lum = 0.299f*r + 0.587f*g + 0.114f*b;
            r = clampf(lum + sat*(r - lum), 0,1) * bright;
            g = clampf(lum + sat*(g - lum), 0,1) * bright;
            b = clampf(lum + sat*(b - lum), 0,1) * bright;
            pix[i]   = clamp8((int)(clampf(r,0,1)*255));
            pix[i+1] = clamp8((int)(clampf(g,0,1)*255));
            pix[i+2] = clamp8((int)(clampf(b,0,1)*255));
        }
    }
}

/**
 * augment_center_crop
 * Crops pct*100% from each side then writes result starting at pix[0].
 * Output size = w*(1-2*crop) x h*(1-2*crop), padded back to original by scaling.
 * For simplicity: crop region is extracted and placed at origin; caller resizes via PIL.
 * Returns new dimensions via out_w / out_h.
 */
void augment_center_crop(uint8_t* pix, int w, int h, float crop_pct,
                          int* out_w, int* out_h) {
    int cx = (int)(w * crop_pct / 2.0f);
    int cy = (int)(h * crop_pct / 2.0f);
    int nw = w - 2*cx, nh = h - 2*cy;
    if (nw <= 0 || nh <= 0) { *out_w = w; *out_h = h; return; }

    /* Extract crop into a temp buffer then copy back */
    uint8_t* tmp = (uint8_t*)malloc(nw * nh * 3);
    if (!tmp) { *out_w = w; *out_h = h; return; }

    #pragma omp parallel for schedule(static)
    for (int y = 0; y < nh; y++) {
        memcpy(tmp + y*nw*3, pix + ((y+cy)*w+cx)*3, nw*3);
    }
    memcpy(pix, tmp, nw*nh*3);
    free(tmp);
    *out_w = nw; *out_h = nh;
}

/**
 * augment_rotate90
 * direction: 1 = CW, -1 = CCW
 * Writes transposed image; caller must swap w/h after this call.
 */
void augment_rotate90(uint8_t* pix, int w, int h, int direction) {
    int sz = w * h * 3;
    uint8_t* tmp = (uint8_t*)malloc(sz);
    if (!tmp) return;
    memcpy(tmp, pix, sz);

    if (direction >= 0) { /* CW */
        #pragma omp parallel for schedule(static)
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
                memcpy(pix + (x*h + (h-1-y))*3, tmp + (y*w+x)*3, 3);
    } else {              /* CCW */
        #pragma omp parallel for schedule(static)
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
                memcpy(pix + ((w-1-x)*h + y)*3, tmp + (y*w+x)*3, 3);
    }
    free(tmp);
}

/**
 * get_thread_count — helper so Python knows how many OMP threads are active
 */
int get_thread_count() {
    return omp_get_max_threads();
}

} /* extern "C" */
