#include <stdbool.h>
#include <stdint.h>

typedef struct {
    double kp, ki, kd;
    double min, max;
    double errorSum;
    double lastError;
    double lastMeasurement;
    double lastOutput;
    uint32_t lastTime;
    bool initialized;
} PID;

void PID_init(PID *pid, double kp, double ki, double kd, double min, double max) {
    pid->kp             = kp;
    pid->ki             = ki;
    pid->kd             = kd;
    pid->min            = min;
    pid->max            = max;
    pid->errorSum       = 0.0;
    pid->lastError      = 0.0;
    pid->lastMeasurement = 0.0;
    pid->lastOutput     = 0.0;
    pid->lastTime       = 0;
    pid->initialized    = false;
}

static double clamp(double val, double lo, double hi) {
    if (val > hi) return hi;
    if (val < lo) return lo;
    return val;
}

double PID_compute(PID *pid, double actual, double desired, uint32_t now) {
    if (!pid->initialized) {
        pid->lastMeasurement = actual;
        pid->lastTime        = now;
        pid->initialized     = true;
        return 0.0;
    }

    uint32_t dT = now - pid->lastTime;
    if (dT == 0) return pid->lastOutput;

    double dt = dT / 1000.0; // ms → seconds

    double error    = desired - actual;
    double errorInt = 0.5 * (error + pid->lastError) * dt;

    pid->errorSum += pid->ki * errorInt;
    pid->errorSum  = clamp(pid->errorSum, pid->min, pid->max); // Integral clamp

    double errorRate = -(actual - pid->lastMeasurement) / dt;  // Derivative on measurement

    double output  = pid->kp * error + pid->errorSum + pid->kd * errorRate;
    double clamped = clamp(output, pid->min, pid->max);

    // Back-calculation anti-windup
    if (output != clamped) {
        pid->errorSum -= (output - clamped);
    }

    pid->lastError       = error;
    pid->lastMeasurement = actual;
    pid->lastTime        = now;
    pid->lastOutput      = clamped;

    return clamped;
}

void PID_reset(PID *pid) {
    pid->errorSum        = 0.0;
    pid->lastError       = 0.0;
    pid->lastMeasurement = 0.0;
    pid->initialized     = false;
}

void PID_setTunings(PID *pid, double kp, double ki, double kd) {
    pid->kp = kp;
    pid->ki = ki;
    pid->kd = kd;
}

void PID_setOutputLimits(PID *pid, double min, double max) {
    pid->min = min;
    pid->max = max;
}