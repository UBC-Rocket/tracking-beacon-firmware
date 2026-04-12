#ifndef SETUP_H
#define SETUP_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

/* Blocks until the user finishes manual alignment.
   Call after Stepper_Init, before Passthrough/RSSI init. */
void Setup_ManualAlign(void);

/* Call after Passthrough_Init to enable non-blocking manual control. */
void Setup_Init(void);

/* Process wasd keys from UART2 DMA buffer. Call from main loop. */
void Setup_Poll(void);

/* Call from HAL_UARTEx_RxEventCallback for UART2 write position tracking. */
void Setup_HandleRxEvent(UART_HandleTypeDef *huart, uint16_t Size);

#endif /* SETUP_H */
