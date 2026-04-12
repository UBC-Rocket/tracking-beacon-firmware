#ifndef STEPPER_H
#define STEPPER_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

typedef enum {
    STEPPER_AZ = 0, /* Azimuth  — PB1 step, PB13 dir */
    STEPPER_EL = 1, /* Elevation — PB14 step, PB15 dir */
    STEPPER_COUNT = 2
} StepperAxis;

typedef enum {
    STEPPER_CW  = 0,
    STEPPER_CCW = 1
} StepperDir;

void Stepper_Init(void);
void Stepper_Step(StepperAxis axis, StepperDir dir);
int32_t Stepper_GetPosition(StepperAxis axis);
void Stepper_ZeroPosition(StepperAxis axis);
void Stepper_SetTarget(StepperAxis axis, int32_t target);
void Stepper_SetSpeed(StepperAxis axis, uint32_t step_interval_ms);
void Stepper_Poll(void);

#endif /* STEPPER_H */
