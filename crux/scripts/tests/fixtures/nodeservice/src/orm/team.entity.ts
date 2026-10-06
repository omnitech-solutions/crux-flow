// TypeORM entity — the @ManyToOne target of Account and the resolved target of
// the `./team.entity` relative import (module-graph edge).
import { Entity, Column, PrimaryGeneratedColumn } from "typeorm";

@Entity("teams")
export class Team {
  @PrimaryGeneratedColumn()
  id: number;

  @Column()
  name: string;
}
