#ifndef RSSI_H
#define RSSI_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

#define RSSI_NUM_ANTENNAS 5
#define RSSI_NUM_OUTER    4
#define RSSI_RX_BUF_SIZE  512

/* Antenna indices */
#define RSSI_ANT_OUTER1   0  /* USART3 */
#define RSSI_ANT_OUTER2   1  /* UART4  */
#define RSSI_ANT_OUTER3   2  /* UART5  */
#define RSSI_ANT_OUTER4   3  /* USART6 */
#define RSSI_ANT_CENTER   4  /* USART1 */

typedef struct {
    uint32_t byte_count;    /* total bytes received */
    uint32_t timestamp;     /* HAL_GetTick() of last received byte */
    uint8_t  active;        /* 1 if bytes received recently */
} RssiReading;

void RSSI_Init(void);
void RSSI_Poll(void);
void RSSI_HandleRxEvent(UART_HandleTypeDef *huart, uint16_t Size);
const RssiReading *RSSI_GetReading(uint8_t antenna_index);

#endif /* RSSI_H */
