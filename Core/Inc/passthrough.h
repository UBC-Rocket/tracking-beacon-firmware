#ifndef PASSTHROUGH_H
#define PASSTHROUGH_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

#define PT_BUF_SIZE 512

typedef struct {
    UART_HandleTypeDef *rx_uart;
    UART_HandleTypeDef *tx_uart;
    uint8_t rx_buf[PT_BUF_SIZE];
    uint8_t tx_buf[PT_BUF_SIZE];
    volatile uint16_t last_pos;
    volatile uint8_t tx_busy;
} PassthroughChannel;

void Passthrough_Init(void);
void Passthrough_HandleRxEvent(UART_HandleTypeDef *huart, uint16_t Size);
void Passthrough_HandleTxCplt(UART_HandleTypeDef *huart);
const uint8_t *Passthrough_GetCenterRxBuf(void);
const uint8_t *Passthrough_GetPcRxBuf(void);

#endif /* PASSTHROUGH_H */
