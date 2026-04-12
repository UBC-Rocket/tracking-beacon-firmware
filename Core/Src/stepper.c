#include "stepper.h"

typedef struct {
    GPIO_TypeDef *step_port;
    uint16_t step_pin;
    GPIO_TypeDef *dir_port;
    uint16_t dir_pin;
    volatile int32_t position;
    int32_t target;
    uint32_t step_interval_ms;
    uint32_t last_step_tick;
} StepperMotor;

static StepperMotor motors[STEPPER_COUNT];

/* ~60 us pulse delay at 84 MHz */
static inline void step_pulse_delay(void)
{
    //84MHz => 84 clock cycles ~1 microsecond
    //TODO: should we not use a hardware timer? the timing can vary but this is fine for now
    for (int i = 0; i < 84; i++)
        __NOP();
}

void Stepper_Init(void)
{
    /* Azimuth: PB1 step, PB13 dir */
    motors[STEPPER_AZ].step_port = GPIOB;
    motors[STEPPER_AZ].step_pin  = GPIO_PIN_1;
    motors[STEPPER_AZ].dir_port  = GPIOB;
    motors[STEPPER_AZ].dir_pin   = GPIO_PIN_13;
    motors[STEPPER_AZ].position  = 0;
    motors[STEPPER_AZ].target    = 0;
    motors[STEPPER_AZ].step_interval_ms = 2;
    motors[STEPPER_AZ].last_step_tick   = 0;

    /* Elevation: PB14 step, PB15 dir */
    motors[STEPPER_EL].step_port = GPIOB;
    motors[STEPPER_EL].step_pin  = GPIO_PIN_14;
    motors[STEPPER_EL].dir_port  = GPIOB;
    motors[STEPPER_EL].dir_pin   = GPIO_PIN_15;
    motors[STEPPER_EL].position  = 0;
    motors[STEPPER_EL].target    = 0;
    motors[STEPPER_EL].step_interval_ms = 2;
    motors[STEPPER_EL].last_step_tick   = 0;
}

void Stepper_Step(StepperAxis axis, StepperDir dir)
{
    StepperMotor *m = &motors[axis];

    HAL_GPIO_WritePin(m->dir_port, m->dir_pin,
                      dir == STEPPER_CW ? GPIO_PIN_SET : GPIO_PIN_RESET);

    HAL_GPIO_WritePin(m->step_port, m->step_pin, GPIO_PIN_SET);
    step_pulse_delay();
    HAL_GPIO_WritePin(m->step_port, m->step_pin, GPIO_PIN_RESET);

    m->position += (dir == STEPPER_CW) ? 1 : -1;
}

int32_t Stepper_GetPosition(StepperAxis axis)
{
    return motors[axis].position;
}

void Stepper_ZeroPosition(StepperAxis axis)
{
    motors[axis].position = 0;
    motors[axis].target   = 0;
}

void Stepper_SetTarget(StepperAxis axis, int32_t target)
{
    motors[axis].target = target;
}

void Stepper_SetSpeed(StepperAxis axis, uint32_t step_interval_ms)
{
    if (step_interval_ms < 1)
        step_interval_ms = 1;
    motors[axis].step_interval_ms = step_interval_ms;
}

void Stepper_Poll(void)
{
    uint32_t now = HAL_GetTick();

    for (int i = 0; i < STEPPER_COUNT; i++) {
        StepperMotor *m = &motors[i];

        if (m->position == m->target)
            continue;

        if ((now - m->last_step_tick) < m->step_interval_ms)
            continue;

        StepperDir dir = (m->target > m->position) ? STEPPER_CW : STEPPER_CCW;
        Stepper_Step(i, dir);
        m->last_step_tick = now;
    }
}
